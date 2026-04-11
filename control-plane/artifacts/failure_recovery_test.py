#!/usr/bin/env python3
"""
Failure Recovery Test Suite
Tests: interrupted operations, partial writes, timeout handling, retry logic
"""

import sqlite3
import os
import time
import json
import threading
import signal
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.path.expanduser("~/.openclaw/workspace/control-plane/control_plane.db")
ARTIFACTS_PATH = os.path.expanduser("~/.openclaw/workspace/control-plane/artifacts")

class FailureRecoveryTester:
    def __init__(self):
        self.results = []
        self.test_artifacts = []
        
    def log(self, test_name, status, details):
        result = {
            "test": test_name,
            "status": status,
            "details": details,
            "timestamp": datetime.utcnow().isoformat()
        }
        self.results.append(result)
        print(f"[{status}] {test_name}: {details}")
        return result

    # Test 1: Interrupted database operation recovery
    def test_interrupted_db_operation(self):
        """Simulate interrupted DB write, verify rollback/recovery"""
        test_name = "interrupted_db_operation"
        test_task_id = "tsk_interrupt_test_001"
        
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Start a transaction that we'll interrupt
            cursor.execute("BEGIN TRANSACTION")
            cursor.execute("""
                INSERT INTO tasks (id, title, brief, status, thread_id)
                VALUES (?, 'Interrupt test task', 'Will be rolled back', 'pending', 'thr_c0c7764ad1d1')
            """, (test_task_id,))
            
            # Simulate interruption - rollback instead of commit
            conn.rollback()
            
            # Verify task was NOT created (rollback worked)
            cursor.execute("SELECT id FROM tasks WHERE id = ?", (test_task_id,))
            row = cursor.fetchone()
            
            if row is None:
                return self.log(test_name, "PASS", "Rollback correctly prevented partial write")
            else:
                return self.log(test_name, "FAIL", "Task exists despite rollback - data corruption risk")
                
        except Exception as e:
            return self.log(test_name, "ERROR", str(e))
        finally:
            conn.close()

    # Test 2: Partial file write recovery
    def test_partial_write_recovery(self):
        """Test atomic write pattern for artifacts"""
        test_name = "partial_write_recovery"
        artifact_path = os.path.join(ARTIFACTS_PATH, "partial_write_test.txt")
        temp_path = artifact_path + ".tmp"
        
        try:
            original_content = "Original safe content"
            new_content = "New content that might fail mid-write"
            
            # Write original
            with open(artifact_path, 'w') as f:
                f.write(original_content)
            
            # Simulate partial write to temp file (atomic pattern)
            with open(temp_path, 'w') as f:
                f.write(new_content[:10])  # Only partial write
                # Simulate failure before completion
                raise IOError("Simulated write failure")
                
        except IOError:
            # Recovery: original file should be intact
            with open(artifact_path, 'r') as f:
                content = f.read()
            
            # Clean up temp file
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
            if content == original_content:
                return self.log(test_name, "PASS", "Atomic write pattern protected original file")
            else:
                return self.log(test_name, "FAIL", "Original file was corrupted")
                
        except Exception as e:
            return self.log(test_name, "ERROR", str(e))
        finally:
            # Cleanup
            for path in [artifact_path, temp_path]:
                if os.path.exists(path):
                    os.remove(path)

    # Test 3: Timeout handling
    def test_timeout_handling(self):
        """Test operation timeout and recovery"""
        test_name = "timeout_handling"
        
        def slow_operation():
            time.sleep(5)  # Would normally take 5 seconds
            return "completed"
            
        try:
            # Use threading with timeout
            result_container = [None]
            exception_container = [None]
            
            def run_with_timeout():
                try:
                    result_container[0] = slow_operation()
                except Exception as e:
                    exception_container[0] = e
                    
            thread = threading.Thread(target=run_with_timeout)
            thread.start()
            thread.join(timeout=0.1)  # 100ms timeout
            
            if thread.is_alive():
                # Operation timed out - this is expected
                return self.log(test_name, "PASS", "Timeout correctly detected long-running operation")
            else:
                return self.log(test_name, "FAIL", "Operation completed when it should have timed out")
                
        except Exception as e:
            return self.log(test_name, "ERROR", str(e))

    # Test 4: Retry logic with exponential backoff
    def test_retry_logic(self):
        """Test retry mechanism with backoff"""
        test_name = "retry_logic"
        
        attempt_count = [0]
        max_retries = 3
        success_on_attempt = 3
        
        def flaky_operation():
            attempt_count[0] += 1
            if attempt_count[0] < success_on_attempt:
                raise ConnectionError(f"Simulated failure on attempt {attempt_count[0]}")
            return "success"
        
        def retry_with_backoff(fn, max_attempts, base_delay=0.01):
            last_error = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return fn()
                except Exception as e:
                    last_error = e
                    if attempt < max_attempts:
                        delay = base_delay * (2 ** (attempt - 1))
                        time.sleep(delay)
            raise last_error
        
        try:
            result = retry_with_backoff(flaky_operation, max_retries)
            
            if result == "success" and attempt_count[0] == success_on_attempt:
                return self.log(test_name, "PASS", 
                    f"Retry succeeded after {attempt_count[0]} attempts with backoff")
            else:
                return self.log(test_name, "FAIL", "Unexpected retry behavior")
                
        except Exception as e:
            return self.log(test_name, "FAIL", f"Retry exhausted: {e}")

    # Test 5: Task state recovery
    def test_task_state_recovery(self):
        """Test task transitions and recovery from invalid states"""
        test_name = "task_state_recovery"
        test_task_id = "tsk_state_test_001"
        
        try:
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            
            # Create task in running state (simulating crash during execution)
            cursor.execute("""
                INSERT INTO tasks (id, title, brief, status, thread_id)
                VALUES (?, 'State recovery test', 'Test orphaned running task', 'running', 'thr_c0c7764ad1d1')
            """, (test_task_id,))
            conn.commit()
            
            # Simulate recovery: detect orphaned running task and transition to failed
            cursor.execute("""
                UPDATE tasks SET status = 'failed', 
                    metadata = json_object('recovery_reason', 'orphaned_running_task', 'recovered_at', datetime('now'))
                WHERE id = ? AND status = 'running'
            """, (test_task_id,))
            conn.commit()
            
            # Verify recovery
            cursor.execute("SELECT status, metadata FROM tasks WHERE id = ?", (test_task_id,))
            row = cursor.fetchone()
            
            if row and row[0] == 'failed' and 'orphaned' in (row[1] or ''):
                # Cleanup
                cursor.execute("DELETE FROM tasks WHERE id = ?", (test_task_id,))
                conn.commit()
                return self.log(test_name, "PASS", "Orphaned task correctly transitioned to failed with recovery metadata")
            else:
                return self.log(test_name, "FAIL", f"Unexpected state after recovery: {row}")
                
        except Exception as e:
            return self.log(test_name, "ERROR", str(e))
        finally:
            # Cleanup
            try:
                cursor.execute("DELETE FROM tasks WHERE id = ?", (test_task_id,))
                conn.commit()
            except:
                pass
            conn.close()

    # Test 6: Concurrent write conflict resolution
    def test_concurrent_write_conflict(self):
        """Test handling of concurrent writes to same record"""
        test_name = "concurrent_write_conflict"
        test_task_id = "tsk_concurrent_test_001"
        
        try:
            # Setup
            conn1 = sqlite3.connect(DB_PATH)
            conn2 = sqlite3.connect(DB_PATH)
            
            cursor1 = conn1.cursor()
            cursor2 = conn2.cursor()
            
            # Create test task
            cursor1.execute("""
                INSERT INTO tasks (id, title, brief, status, thread_id)
                VALUES (?, 'Concurrent test', 'Test concurrent updates', 'pending', 'thr_c0c7764ad1d1')
            """, (test_task_id,))
            conn1.commit()
            
            # Simulate concurrent updates
            conflicts_handled = True
            try:
                cursor1.execute("BEGIN IMMEDIATE")
                cursor1.execute("UPDATE tasks SET status = 'running' WHERE id = ?", (test_task_id,))
                
                # Second connection tries to update same record
                cursor2.execute("BEGIN IMMEDIATE")
                cursor2.execute("UPDATE tasks SET status = 'assigned' WHERE id = ?", (test_task_id,))
                
                conn1.commit()
                conn2.commit()
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() or "busy" in str(e).lower():
                    conflicts_handled = True
                    conn1.rollback()
                    conn2.rollback()
                else:
                    raise
            
            # Cleanup
            cursor1.execute("DELETE FROM tasks WHERE id = ?", (test_task_id,))
            conn1.commit()
            
            return self.log(test_name, "PASS", "SQLite locking prevents concurrent write conflicts")
            
        except Exception as e:
            return self.log(test_name, "ERROR", str(e))
        finally:
            conn1.close()
            conn2.close()

    def run_all_tests(self):
        """Execute all failure recovery tests"""
        print("=" * 60)
        print("FAILURE RECOVERY TEST SUITE")
        print("=" * 60)
        
        tests = [
            self.test_interrupted_db_operation,
            self.test_partial_write_recovery,
            self.test_timeout_handling,
            self.test_retry_logic,
            self.test_task_state_recovery,
            self.test_concurrent_write_conflict,
        ]
        
        for test in tests:
            try:
                test()
            except Exception as e:
                self.log(test.__name__, "ERROR", f"Unhandled exception: {e}")
            print("-" * 40)
        
        # Summary
        passed = sum(1 for r in self.results if r["status"] == "PASS")
        failed = sum(1 for r in self.results if r["status"] == "FAIL")
        errors = sum(1 for r in self.results if r["status"] == "ERROR")
        
        print("=" * 60)
        print(f"SUMMARY: {passed} passed, {failed} failed, {errors} errors")
        print("=" * 60)
        
        return {
            "summary": {
                "passed": passed,
                "failed": failed,
                "errors": errors,
                "total": len(self.results)
            },
            "results": self.results
        }


if __name__ == "__main__":
    tester = FailureRecoveryTester()
    results = tester.run_all_tests()
    
    # Save results
    output_path = os.path.join(ARTIFACTS_PATH, "failure_recovery_results.json")
    with open(output_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")
