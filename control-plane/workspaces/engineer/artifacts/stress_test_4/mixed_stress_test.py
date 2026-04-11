#!/usr/bin/env python3
"""
Mixed Read/Write Stress Test for SQLite WAL database.
50 readers + 50 writers running in parallel for 60 seconds.
"""

import sqlite3
import threading
import time
import random
import json
import os
import statistics
from dataclasses import dataclass, field
from typing import List
from concurrent.futures import ThreadPoolExecutor

DB_PATH = "/home/ubuntu/.openclaw/workspace/control-plane/workspaces/engineer/artifacts/stress_test_4/stress_test.db"
ARTIFACT_DIR = "/home/ubuntu/.openclaw/workspace/control-plane/workspaces/engineer/artifacts/stress_test_4"
NUM_READERS = 50
NUM_WRITERS = 50
DURATION_SECONDS = 60
BASELINE_OPS_SEC = 834
BASELINE_P99_SEC = 2.7

@dataclass
class WorkerStats:
    latencies: List[float] = field(default_factory=list)
    errors: int = 0
    ops: int = 0

def setup_database():
    """Create WAL-mode database with test table."""
    os.makedirs(ARTIFACT_DIR, exist_ok=True)
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS test_data (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            created_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_key ON test_data(key)")
    # Pre-populate with some data for readers
    for i in range(1000):
        conn.execute("INSERT INTO test_data (key, value, created_at) VALUES (?, ?, ?)",
                     (f"key_{i}", f"value_{i}", time.time()))
    conn.commit()
    conn.close()
    print(f"Database created at {DB_PATH} with WAL mode")

def writer_worker(worker_id: int, stop_event: threading.Event, stats: WorkerStats):
    """Writer thread: performs INSERT operations."""
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.execute("PRAGMA busy_timeout=30000")
    
    while not stop_event.is_set():
        try:
            start = time.perf_counter()
            key = f"writer_{worker_id}_{random.randint(0, 1000000)}"
            value = f"data_{random.randint(0, 1000000)}"
            conn.execute("INSERT INTO test_data (key, value, created_at) VALUES (?, ?, ?)",
                        (key, value, time.time()))
            conn.commit()
            elapsed = time.perf_counter() - start
            stats.latencies.append(elapsed)
            stats.ops += 1
        except Exception as e:
            stats.errors += 1
    
    conn.close()

def reader_worker(worker_id: int, stop_event: threading.Event, stats: WorkerStats):
    """Reader thread: performs SELECT operations."""
    conn = sqlite3.connect(DB_PATH, timeout=60.0)
    conn.execute("PRAGMA busy_timeout=30000")
    
    while not stop_event.is_set():
        try:
            start = time.perf_counter()
            # Random read patterns
            pattern = random.choice(['single', 'range', 'count'])
            if pattern == 'single':
                key_id = random.randint(0, 999)
                conn.execute("SELECT * FROM test_data WHERE key = ?", (f"key_{key_id}",)).fetchall()
            elif pattern == 'range':
                start_id = random.randint(0, 900)
                conn.execute("SELECT * FROM test_data WHERE id BETWEEN ? AND ?", 
                            (start_id, start_id + 100)).fetchall()
            else:
                conn.execute("SELECT COUNT(*) FROM test_data").fetchone()
            elapsed = time.perf_counter() - start
            stats.latencies.append(elapsed)
            stats.ops += 1
        except Exception as e:
            stats.errors += 1
    
    conn.close()

def compute_percentile(data: List[float], p: float) -> float:
    """Compute percentile of a list."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * p / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]

def run_stress_test():
    """Run the mixed read/write stress test."""
    setup_database()
    
    stop_event = threading.Event()
    reader_stats = [WorkerStats() for _ in range(NUM_READERS)]
    writer_stats = [WorkerStats() for _ in range(NUM_WRITERS)]
    
    threads = []
    
    print(f"Starting {NUM_READERS} readers + {NUM_WRITERS} writers for {DURATION_SECONDS}s...")
    start_time = time.time()
    
    # Start readers
    for i in range(NUM_READERS):
        t = threading.Thread(target=reader_worker, args=(i, stop_event, reader_stats[i]))
        t.start()
        threads.append(t)
    
    # Start writers
    for i in range(NUM_WRITERS):
        t = threading.Thread(target=writer_worker, args=(i, stop_event, writer_stats[i]))
        t.start()
        threads.append(t)
    
    # Wait for duration
    time.sleep(DURATION_SECONDS)
    stop_event.set()
    
    # Join all threads
    for t in threads:
        t.join(timeout=10)
    
    end_time = time.time()
    actual_duration = end_time - start_time
    
    # Aggregate stats
    all_read_latencies = []
    all_write_latencies = []
    total_read_ops = 0
    total_write_ops = 0
    total_read_errors = 0
    total_write_errors = 0
    
    for s in reader_stats:
        all_read_latencies.extend(s.latencies)
        total_read_ops += s.ops
        total_read_errors += s.errors
    
    for s in writer_stats:
        all_write_latencies.extend(s.latencies)
        total_write_ops += s.ops
        total_write_errors += s.errors
    
    total_ops = total_read_ops + total_write_ops
    total_throughput = total_ops / actual_duration
    read_throughput = total_read_ops / actual_duration
    write_throughput = total_write_ops / actual_duration
    
    # Compute latency stats
    def latency_stats(latencies: List[float]) -> dict:
        if not latencies:
            return {"mean": 0, "p50": 0, "p95": 0, "p99": 0, "max": 0}
        return {
            "mean": statistics.mean(latencies),
            "p50": compute_percentile(latencies, 50),
            "p95": compute_percentile(latencies, 95),
            "p99": compute_percentile(latencies, 99),
            "max": max(latencies)
        }
    
    read_lat = latency_stats(all_read_latencies)
    write_lat = latency_stats(all_write_latencies)
    
    results = {
        "test_config": {
            "num_readers": NUM_READERS,
            "num_writers": NUM_WRITERS,
            "duration_seconds": DURATION_SECONDS,
            "actual_duration_seconds": actual_duration,
            "database_path": DB_PATH
        },
        "throughput": {
            "total_ops": total_ops,
            "total_ops_per_sec": total_throughput,
            "read_ops": total_read_ops,
            "read_ops_per_sec": read_throughput,
            "write_ops": total_write_ops,
            "write_ops_per_sec": write_throughput
        },
        "read_latency_seconds": read_lat,
        "write_latency_seconds": write_lat,
        "errors": {
            "read_errors": total_read_errors,
            "write_errors": total_write_errors,
            "total_errors": total_read_errors + total_write_errors
        },
        "baseline_comparison": {
            "baseline_write_ops_per_sec": BASELINE_OPS_SEC,
            "baseline_p99_sec": BASELINE_P99_SEC,
            "mixed_write_vs_baseline_throughput": f"{(write_throughput / BASELINE_OPS_SEC * 100):.1f}%",
            "mixed_write_p99_vs_baseline": f"{(write_lat['p99'] / BASELINE_P99_SEC * 100):.1f}%"
        }
    }
    
    # Save results
    results_path = os.path.join(ARTIFACT_DIR, "results.json")
    with open(results_path, 'w') as f:
        json.dump(results, f, indent=2)
    print(f"Results saved to {results_path}")
    
    # Generate report
    report = f"""# Mixed Read/Write Stress Test Report

## Test Configuration
- **Readers**: {NUM_READERS}
- **Writers**: {NUM_WRITERS}
- **Duration**: {actual_duration:.1f} seconds
- **Database**: SQLite WAL mode

## Throughput Results

| Metric | Value |
|--------|-------|
| Total Operations | {total_ops:,} |
| Total Throughput | {total_throughput:,.1f} ops/sec |
| Read Operations | {total_read_ops:,} |
| Read Throughput | {read_throughput:,.1f} ops/sec |
| Write Operations | {total_write_ops:,} |
| Write Throughput | {write_throughput:,.1f} ops/sec |

## Read Latencies (under write contention)

| Percentile | Latency |
|------------|---------|
| Mean | {read_lat['mean']*1000:.2f} ms |
| p50 | {read_lat['p50']*1000:.2f} ms |
| p95 | {read_lat['p95']*1000:.2f} ms |
| p99 | {read_lat['p99']*1000:.2f} ms |
| Max | {read_lat['max']*1000:.2f} ms |

## Write Latencies (under read contention)

| Percentile | Latency |
|------------|---------|
| Mean | {write_lat['mean']*1000:.2f} ms |
| p50 | {write_lat['p50']*1000:.2f} ms |
| p95 | {write_lat['p95']*1000:.2f} ms |
| p99 | {write_lat['p99']*1000:.2f} ms |
| Max | {write_lat['max']*1000:.2f} ms |

## Baseline Comparison

| Metric | Mixed Test | Baseline | Ratio |
|--------|-----------|----------|-------|
| Write Throughput | {write_throughput:,.1f} ops/sec | {BASELINE_OPS_SEC} ops/sec | {(write_throughput / BASELINE_OPS_SEC * 100):.1f}% |
| Write p99 | {write_lat['p99']:.3f}s | {BASELINE_P99_SEC}s | {(write_lat['p99'] / BASELINE_P99_SEC * 100):.1f}% |

## Errors
- Read Errors: {total_read_errors}
- Write Errors: {total_write_errors}

## Analysis

{"✅ **Write throughput maintained well** under read contention." if write_throughput > BASELINE_OPS_SEC * 0.5 else "⚠️ **Write throughput degraded significantly** under read contention."}

{"✅ **Write latency p99 acceptable**." if write_lat['p99'] < BASELINE_P99_SEC * 2 else "⚠️ **Write latency p99 degraded** significantly."}

{"✅ **No errors**." if (total_read_errors + total_write_errors) == 0 else f"⚠️ **{total_read_errors + total_write_errors} errors** occurred during test."}

SQLite WAL mode allows concurrent readers during writes, which explains the read performance characteristics under write contention.
"""
    
    report_path = os.path.join(ARTIFACT_DIR, "REPORT.md")
    with open(report_path, 'w') as f:
        f.write(report)
    print(f"Report saved to {report_path}")
    
    # Print summary
    print("\n" + "="*60)
    print("STRESS TEST COMPLETE")
    print("="*60)
    print(f"Total throughput: {total_throughput:,.1f} ops/sec")
    print(f"Read throughput:  {read_throughput:,.1f} ops/sec")
    print(f"Write throughput: {write_throughput:,.1f} ops/sec")
    print(f"Read p99:  {read_lat['p99']*1000:.2f} ms")
    print(f"Write p99: {write_lat['p99']*1000:.2f} ms")
    print(f"Errors: {total_read_errors + total_write_errors}")
    print("="*60)
    
    return results

if __name__ == "__main__":
    run_stress_test()
