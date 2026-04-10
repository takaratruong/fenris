#!/usr/bin/env python3
"""
Write Queue Pattern Implementation for Baseline C Evaluation (Method 1)

Implements a centralized write queue that serializes SQLite writes through
a single writer thread while allowing concurrent read access.

Pattern: All write operations go through a queue, processed by a dedicated
writer thread, eliminating SQLITE_BUSY errors from concurrent writes.
"""

import sqlite3
import threading
import queue
import time
import uuid
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Callable, List
from contextlib import contextmanager


@dataclass
class WriteOperation:
    """Represents a queued write operation."""
    op_id: str
    sql: str
    params: tuple
    callback: Optional[Callable] = None
    enqueue_time: float = 0.0
    result_event: threading.Event = None
    result: Any = None
    error: Optional[Exception] = None
    
    def __post_init__(self):
        self.enqueue_time = time.time()
        self.result_event = threading.Event()


class WriteQueue:
    """
    Centralized write queue for SQLite.
    
    - Single writer thread processes all writes sequentially
    - Eliminates SQLITE_BUSY from concurrent write attempts
    - Concurrent reads are still allowed via separate connections
    - Callbacks notify callers when their write completes
    """
    
    def __init__(self, db_path: str, busy_timeout: int = 5000):
        self.db_path = db_path
        self.busy_timeout = busy_timeout
        self._queue: queue.Queue = queue.Queue()
        self._writer_thread: Optional[threading.Thread] = None
        self._running = False
        self._conn: Optional[sqlite3.Connection] = None
        
        # Metrics
        self._writes_completed = 0
        self._writes_failed = 0
        self._total_write_time = 0.0
        self._busy_count = 0
        self._lock = threading.Lock()
        
    def start(self):
        """Start the writer thread."""
        if self._running:
            return
            
        self._running = True
        self._writer_thread = threading.Thread(target=self._writer_loop, daemon=True)
        self._writer_thread.start()
        
    def stop(self, timeout: float = 5.0):
        """Stop the writer thread and wait for pending writes."""
        if not self._running:
            return
            
        self._running = False
        # Signal shutdown with None
        self._queue.put(None)
        
        if self._writer_thread:
            self._writer_thread.join(timeout=timeout)
            
    def _writer_loop(self):
        """Main writer loop - processes writes sequentially."""
        # Create dedicated write connection
        self._conn = sqlite3.connect(self.db_path, timeout=self.busy_timeout / 1000.0)
        self._conn.execute(f"PRAGMA busy_timeout = {self.busy_timeout}")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        
        while self._running or not self._queue.empty():
            try:
                op = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
                
            if op is None:  # Shutdown signal
                break
                
            self._process_write(op)
            
        if self._conn:
            self._conn.close()
            self._conn = None
            
    def _process_write(self, op: WriteOperation):
        """Process a single write operation."""
        start_time = time.time()
        try:
            cursor = self._conn.cursor()
            cursor.execute(op.sql, op.params)
            self._conn.commit()
            op.result = cursor.lastrowid
            
            with self._lock:
                self._writes_completed += 1
                self._total_write_time += time.time() - start_time
                
        except sqlite3.OperationalError as e:
            if "database is locked" in str(e) or "SQLITE_BUSY" in str(e):
                with self._lock:
                    self._busy_count += 1
            op.error = e
            with self._lock:
                self._writes_failed += 1
                
        except Exception as e:
            op.error = e
            with self._lock:
                self._writes_failed += 1
                
        finally:
            op.result_event.set()
            if op.callback:
                try:
                    op.callback(op)
                except Exception:
                    pass
                    
    def enqueue_write(self, sql: str, params: tuple = (), 
                      callback: Optional[Callable] = None,
                      wait: bool = True,
                      timeout: float = 30.0) -> WriteOperation:
        """
        Enqueue a write operation.
        
        Args:
            sql: SQL statement to execute
            params: Parameters for the SQL statement
            callback: Optional callback when write completes
            wait: If True, block until write completes
            timeout: Timeout in seconds if waiting
            
        Returns:
            WriteOperation with result/error after completion
        """
        op = WriteOperation(
            op_id=uuid.uuid4().hex[:12],
            sql=sql,
            params=params,
            callback=callback
        )
        self._queue.put(op)
        
        if wait:
            op.result_event.wait(timeout=timeout)
            
        return op
        
    def get_metrics(self) -> Dict[str, Any]:
        """Get queue metrics."""
        with self._lock:
            avg_write_time = (
                self._total_write_time / self._writes_completed 
                if self._writes_completed > 0 else 0
            )
            return {
                "writes_completed": self._writes_completed,
                "writes_failed": self._writes_failed,
                "busy_count": self._busy_count,
                "avg_write_time_ms": avg_write_time * 1000,
                "queue_size": self._queue.qsize()
            }


class StressTestRunner:
    """Runs concurrent write stress tests against the write queue."""
    
    def __init__(self, db_path: str, num_writers: int = 100, duration_sec: int = 60):
        self.db_path = db_path
        self.num_writers = num_writers
        self.duration_sec = duration_sec
        self.write_queue = WriteQueue(db_path)
        
        # Per-writer metrics
        self._writer_stats: Dict[int, Dict[str, Any]] = {}
        self._all_latencies: List[float] = []
        self._latency_lock = threading.Lock()
        
    def setup_database(self):
        """Create test tables."""
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stress_test (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                writer_id INTEGER,
                value TEXT,
                created_at REAL
            )
        """)
        conn.execute("DELETE FROM stress_test")  # Clean slate
        conn.commit()
        conn.close()
        
    def writer_task(self, writer_id: int, stop_event: threading.Event):
        """Simulates a concurrent writer."""
        stats = {
            "writes_attempted": 0,
            "writes_succeeded": 0,
            "writes_failed": 0,
            "latencies": []
        }
        
        while not stop_event.is_set():
            start = time.time()
            
            op = self.write_queue.enqueue_write(
                "INSERT INTO stress_test (writer_id, value, created_at) VALUES (?, ?, ?)",
                (writer_id, f"data_{uuid.uuid4().hex[:8]}", time.time()),
                wait=True,
                timeout=10.0
            )
            
            latency = time.time() - start
            stats["writes_attempted"] += 1
            
            if op.error is None:
                stats["writes_succeeded"] += 1
            else:
                stats["writes_failed"] += 1
                
            stats["latencies"].append(latency * 1000)  # ms
            
            with self._latency_lock:
                self._all_latencies.append(latency * 1000)
                
            # Small delay to prevent CPU spinning
            time.sleep(0.001)
            
        self._writer_stats[writer_id] = stats
        
    def run(self) -> Dict[str, Any]:
        """Run the stress test."""
        print(f"Setting up database at {self.db_path}")
        self.setup_database()
        
        print(f"Starting write queue...")
        self.write_queue.start()
        
        stop_event = threading.Event()
        threads = []
        
        print(f"Launching {self.num_writers} concurrent writers for {self.duration_sec}s...")
        start_time = time.time()
        
        for i in range(self.num_writers):
            t = threading.Thread(target=self.writer_task, args=(i, stop_event))
            t.start()
            threads.append(t)
            
        # Run for duration
        time.sleep(self.duration_sec)
        
        print("Signaling writers to stop...")
        stop_event.set()
        
        for t in threads:
            t.join(timeout=5.0)
            
        elapsed = time.time() - start_time
        
        print("Stopping write queue...")
        self.write_queue.stop()
        
        # Aggregate results
        return self._compute_results(elapsed)
        
    def _compute_results(self, elapsed: float) -> Dict[str, Any]:
        """Compute final results."""
        total_attempted = sum(s["writes_attempted"] for s in self._writer_stats.values())
        total_succeeded = sum(s["writes_succeeded"] for s in self._writer_stats.values())
        total_failed = sum(s["writes_failed"] for s in self._writer_stats.values())
        
        # Latency percentiles
        latencies = sorted(self._all_latencies)
        p50 = latencies[int(len(latencies) * 0.50)] if latencies else 0
        p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0
        p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0
        max_lat = max(latencies) if latencies else 0
        min_lat = min(latencies) if latencies else 0
        avg_lat = sum(latencies) / len(latencies) if latencies else 0
        
        queue_metrics = self.write_queue.get_metrics()
        
        success_rate = total_succeeded / total_attempted if total_attempted > 0 else 0
        throughput = total_succeeded / elapsed if elapsed > 0 else 0
        
        return {
            "test_config": {
                "num_writers": self.num_writers,
                "duration_sec": self.duration_sec,
                "db_path": self.db_path
            },
            "summary": {
                "total_writes_attempted": total_attempted,
                "total_writes_succeeded": total_succeeded,
                "total_writes_failed": total_failed,
                "success_rate": success_rate,
                "throughput_writes_per_sec": throughput,
                "elapsed_sec": elapsed,
                "sqlite_busy_count": queue_metrics["busy_count"]
            },
            "latency_ms": {
                "min": min_lat,
                "avg": avg_lat,
                "p50": p50,
                "p95": p95,
                "p99": p99,
                "max": max_lat
            },
            "queue_metrics": queue_metrics,
            "per_writer_sample": {
                k: {
                    "writes_attempted": v["writes_attempted"],
                    "writes_succeeded": v["writes_succeeded"],
                    "avg_latency_ms": sum(v["latencies"]) / len(v["latencies"]) if v["latencies"] else 0
                }
                for k, v in list(self._writer_stats.items())[:5]  # Sample first 5
            }
        }


def run_method_1_evaluation():
    """Run Method 1: Write Queue Pattern evaluation."""
    import tempfile
    
    # Create temp database
    test_dir = tempfile.mkdtemp(prefix="writequeue_test_")
    db_path = os.path.join(test_dir, "stress_test.db")
    
    print("=" * 60)
    print("Method 1: Write Queue Pattern - Stress Test")
    print("=" * 60)
    print(f"Database: {db_path}")
    print(f"Writers: 100 concurrent")
    print(f"Duration: 60 seconds")
    print("=" * 60)
    
    runner = StressTestRunner(
        db_path=db_path,
        num_writers=100,
        duration_sec=60
    )
    
    results = runner.run()
    
    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Success Rate: {results['summary']['success_rate']:.4f} ({results['summary']['success_rate']*100:.2f}%)")
    print(f"Throughput: {results['summary']['throughput_writes_per_sec']:.2f} writes/sec")
    print(f"P99 Latency: {results['latency_ms']['p99']:.2f} ms")
    print(f"SQLITE_BUSY Count: {results['summary']['sqlite_busy_count']}")
    print("=" * 60)
    
    return results


if __name__ == "__main__":
    results = run_method_1_evaluation()
    print("\nResults JSON:")
    print(json.dumps(results, indent=2))
