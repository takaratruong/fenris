#!/usr/bin/env python3
"""
SQLite Concurrent Write Stress Test

Tests database under heavy concurrent write load:
- 100 concurrent writer threads
- INSERT/UPDATE in tight loop for 60 seconds
- Tracks SQLITE_BUSY and lock errors
- Reports p50/p99 write latency and error rate
- Runs integrity_check after test
"""

import sqlite3
import threading
import time
import statistics
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any
from collections import defaultdict
import random
import string

# Configuration
NUM_WRITERS = 100
TEST_DURATION_SEC = 60
DB_PATH = Path(__file__).parent / "test_stress.db"
RESULTS_PATH = Path(__file__).parent / "results.json"

# SQLite settings for stress test
SQLITE_SETTINGS = {
    "journal_mode": "WAL",
    "synchronous": "NORMAL",
    "busy_timeout": 5000,  # 5 second busy timeout
    "wal_autocheckpoint": 1000,
    "cache_size": -64000,  # 64MB cache
}


@dataclass
class WriterStats:
    """Statistics collected by each writer thread."""
    thread_id: int
    writes_completed: int = 0
    inserts: int = 0
    updates: int = 0
    latencies_ms: List[float] = field(default_factory=list)
    busy_errors: int = 0
    lock_errors: int = 0
    other_errors: int = 0
    error_messages: List[str] = field(default_factory=list)


@dataclass
class TestResults:
    """Aggregated test results."""
    test_id: str
    start_time: str
    end_time: str
    duration_sec: float
    num_writers: int
    db_settings: Dict[str, Any]
    
    # Aggregate metrics
    total_writes: int = 0
    total_inserts: int = 0
    total_updates: int = 0
    total_busy_errors: int = 0
    total_lock_errors: int = 0
    total_other_errors: int = 0
    
    # Latency stats (ms)
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0
    latency_mean_ms: float = 0.0
    latency_max_ms: float = 0.0
    
    # Derived metrics
    writes_per_second: float = 0.0
    error_rate_percent: float = 0.0
    
    # Integrity check
    integrity_check_passed: bool = False
    integrity_check_output: str = ""
    
    # Per-thread breakdown
    per_thread_stats: List[Dict] = field(default_factory=list)
    error_samples: List[str] = field(default_factory=list)


def setup_database(db_path: Path) -> None:
    """Create fresh test database with configured settings."""
    # Remove existing DB
    if db_path.exists():
        db_path.unlink()
    wal_path = db_path.parent / f"{db_path.name}-wal"
    shm_path = db_path.parent / f"{db_path.name}-shm"
    if wal_path.exists():
        wal_path.unlink()
    if shm_path.exists():
        shm_path.unlink()
    
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Apply settings
    for setting, value in SQLITE_SETTINGS.items():
        cursor.execute(f"PRAGMA {setting} = {value}")
    
    # Verify settings applied
    for setting in SQLITE_SETTINGS:
        cursor.execute(f"PRAGMA {setting}")
        actual = cursor.fetchone()[0]
        print(f"  {setting}: {actual}")
    
    # Create test table
    cursor.execute("""
        CREATE TABLE stress_test (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            thread_id INTEGER NOT NULL,
            counter INTEGER NOT NULL,
            data TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT
        )
    """)
    
    # Create index for updates
    cursor.execute("CREATE INDEX idx_thread_id ON stress_test(thread_id)")
    
    conn.commit()
    conn.close()
    print(f"Database created: {db_path}")


def writer_thread(thread_id: int, stop_event: threading.Event, stats: WriterStats) -> None:
    """Writer thread that performs INSERT/UPDATE operations in a tight loop."""
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0)
    cursor = conn.cursor()
    
    # Apply per-connection settings
    cursor.execute(f"PRAGMA busy_timeout = {SQLITE_SETTINGS['busy_timeout']}")
    
    counter = 0
    my_row_ids = []
    
    while not stop_event.is_set():
        try:
            start_time = time.perf_counter()
            
            # Alternate between INSERT and UPDATE
            if counter % 3 == 0 or not my_row_ids:
                # INSERT
                data = ''.join(random.choices(string.ascii_letters, k=100))
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    "INSERT INTO stress_test (thread_id, counter, data, created_at) VALUES (?, ?, ?, ?)",
                    (thread_id, counter, data, now)
                )
                conn.commit()
                my_row_ids.append(cursor.lastrowid)
                if len(my_row_ids) > 100:
                    my_row_ids = my_row_ids[-50:]  # Keep recent IDs
                stats.inserts += 1
            else:
                # UPDATE random owned row
                row_id = random.choice(my_row_ids)
                now = datetime.now(timezone.utc).isoformat()
                cursor.execute(
                    "UPDATE stress_test SET counter = ?, updated_at = ? WHERE id = ?",
                    (counter, now, row_id)
                )
                conn.commit()
                stats.updates += 1
            
            elapsed_ms = (time.perf_counter() - start_time) * 1000
            stats.latencies_ms.append(elapsed_ms)
            stats.writes_completed += 1
            counter += 1
            
        except sqlite3.OperationalError as e:
            error_msg = str(e).lower()
            if "busy" in error_msg or "locked" in error_msg:
                if "busy" in error_msg:
                    stats.busy_errors += 1
                else:
                    stats.lock_errors += 1
            else:
                stats.other_errors += 1
            
            if len(stats.error_messages) < 10:
                stats.error_messages.append(f"Thread {thread_id}: {e}")
            
            # Brief backoff on error
            time.sleep(0.001)
            
        except Exception as e:
            stats.other_errors += 1
            if len(stats.error_messages) < 10:
                stats.error_messages.append(f"Thread {thread_id}: {type(e).__name__}: {e}")
            time.sleep(0.001)
    
    conn.close()


def run_integrity_check(db_path: Path) -> tuple[bool, str]:
    """Run SQLite integrity_check on the database."""
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    cursor.execute("PRAGMA integrity_check")
    result = cursor.fetchall()
    conn.close()
    
    output = "\n".join(row[0] for row in result)
    passed = len(result) == 1 and result[0][0] == "ok"
    return passed, output


def run_stress_test() -> TestResults:
    """Execute the full stress test."""
    test_id = f"sqlite_stress_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    
    print(f"\n{'='*60}")
    print(f"SQLite Concurrent Write Stress Test")
    print(f"Test ID: {test_id}")
    print(f"Writers: {NUM_WRITERS}")
    print(f"Duration: {TEST_DURATION_SEC}s")
    print(f"{'='*60}\n")
    
    # Setup
    print("Setting up database...")
    setup_database(DB_PATH)
    
    # Prepare threads
    stop_event = threading.Event()
    thread_stats = [WriterStats(thread_id=i) for i in range(NUM_WRITERS)]
    threads = [
        threading.Thread(target=writer_thread, args=(i, stop_event, thread_stats[i]))
        for i in range(NUM_WRITERS)
    ]
    
    # Run test
    print(f"\nStarting {NUM_WRITERS} writer threads...")
    start_time = datetime.now(timezone.utc)
    start_perf = time.perf_counter()
    
    for t in threads:
        t.start()
    
    # Progress updates
    for elapsed in range(TEST_DURATION_SEC):
        time.sleep(1)
        total = sum(s.writes_completed for s in thread_stats)
        errors = sum(s.busy_errors + s.lock_errors for s in thread_stats)
        print(f"  [{elapsed+1:3d}s] Writes: {total:,}  Errors: {errors:,}")
    
    # Stop threads
    print("\nStopping writers...")
    stop_event.set()
    for t in threads:
        t.join(timeout=10)
    
    end_time = datetime.now(timezone.utc)
    actual_duration = time.perf_counter() - start_perf
    
    # Collect all latencies
    all_latencies = []
    for s in thread_stats:
        all_latencies.extend(s.latencies_ms)
    
    # Calculate results
    results = TestResults(
        test_id=test_id,
        start_time=start_time.isoformat(),
        end_time=end_time.isoformat(),
        duration_sec=actual_duration,
        num_writers=NUM_WRITERS,
        db_settings=SQLITE_SETTINGS,
        total_writes=sum(s.writes_completed for s in thread_stats),
        total_inserts=sum(s.inserts for s in thread_stats),
        total_updates=sum(s.updates for s in thread_stats),
        total_busy_errors=sum(s.busy_errors for s in thread_stats),
        total_lock_errors=sum(s.lock_errors for s in thread_stats),
        total_other_errors=sum(s.other_errors for s in thread_stats),
    )
    
    # Latency percentiles
    if all_latencies:
        sorted_lat = sorted(all_latencies)
        results.latency_p50_ms = sorted_lat[len(sorted_lat) // 2]
        results.latency_p99_ms = sorted_lat[int(len(sorted_lat) * 0.99)]
        results.latency_mean_ms = statistics.mean(all_latencies)
        results.latency_max_ms = max(all_latencies)
    
    # Derived metrics
    results.writes_per_second = results.total_writes / actual_duration
    total_attempts = results.total_writes + results.total_busy_errors + results.total_lock_errors + results.total_other_errors
    if total_attempts > 0:
        results.error_rate_percent = 100 * (results.total_busy_errors + results.total_lock_errors + results.total_other_errors) / total_attempts
    
    # Integrity check
    print("\nRunning integrity check...")
    results.integrity_check_passed, results.integrity_check_output = run_integrity_check(DB_PATH)
    
    # Per-thread stats and error samples
    results.per_thread_stats = [
        {
            "thread_id": s.thread_id,
            "writes": s.writes_completed,
            "busy_errors": s.busy_errors,
            "lock_errors": s.lock_errors,
        }
        for s in thread_stats
    ]
    
    for s in thread_stats:
        results.error_samples.extend(s.error_messages[:2])
    results.error_samples = results.error_samples[:20]  # Limit samples
    
    return results


def print_results(results: TestResults) -> None:
    """Print formatted results."""
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"Duration:        {results.duration_sec:.2f}s")
    print(f"Total Writes:    {results.total_writes:,}")
    print(f"  - Inserts:     {results.total_inserts:,}")
    print(f"  - Updates:     {results.total_updates:,}")
    print(f"Throughput:      {results.writes_per_second:,.1f} writes/sec")
    print()
    print("LATENCY (ms):")
    print(f"  p50:           {results.latency_p50_ms:.3f}")
    print(f"  p99:           {results.latency_p99_ms:.3f}")
    print(f"  mean:          {results.latency_mean_ms:.3f}")
    print(f"  max:           {results.latency_max_ms:.3f}")
    print()
    print("ERRORS:")
    print(f"  SQLITE_BUSY:   {results.total_busy_errors:,}")
    print(f"  Lock errors:   {results.total_lock_errors:,}")
    print(f"  Other:         {results.total_other_errors:,}")
    print(f"  Error rate:    {results.error_rate_percent:.4f}%")
    print()
    print(f"INTEGRITY CHECK: {'PASSED ✓' if results.integrity_check_passed else 'FAILED ✗'}")
    print(f"  Output: {results.integrity_check_output}")
    
    if results.error_samples:
        print("\nERROR SAMPLES:")
        for msg in results.error_samples[:5]:
            print(f"  - {msg}")
    
    print(f"{'='*60}\n")


def main():
    results = run_stress_test()
    print_results(results)
    
    # Save results
    with open(RESULTS_PATH, 'w') as f:
        json.dump(asdict(results), f, indent=2)
    print(f"Results saved to: {RESULTS_PATH}")
    
    # Return exit code based on integrity check
    return 0 if results.integrity_check_passed else 1


if __name__ == "__main__":
    exit(main())
