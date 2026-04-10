#!/usr/bin/env python3
"""
SQLite WAL vs DELETE mode benchmark
Measures lock contention, transaction throughput, and documents edge cases
"""
import sqlite3
import time
import threading
import statistics
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

RESULTS = {"delete_mode": {}, "wal_mode": {}, "edge_cases": {}}

def create_schema(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY,
            name TEXT,
            status TEXT,
            data BLOB,
            created_at REAL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_status ON tasks(status)")
    conn.commit()

def measure_single_writer(db_path, mode_name, num_writes=500):
    """Measure single-threaded write performance"""
    conn = sqlite3.connect(db_path)
    create_schema(conn)
    
    start = time.perf_counter()
    for i in range(num_writes):
        conn.execute(
            "INSERT INTO tasks (name, status, data, created_at) VALUES (?, ?, ?, ?)",
            (f"task_{i}", "pending", os.urandom(100), time.time())
        )
        conn.commit()  # Commit each write to simulate real workload
    elapsed = time.perf_counter() - start
    conn.close()
    
    return {
        "writes": num_writes,
        "elapsed_s": round(elapsed, 4),
        "writes_per_s": round(num_writes / elapsed, 2)
    }

def measure_concurrent_writers(db_path, mode_name, num_threads=8, writes_per_thread=100):
    """Measure concurrent write performance and lock contention"""
    lock_waits = []
    errors = []
    
    def writer(thread_id):
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        local_lock_waits = []
        
        for i in range(writes_per_thread):
            start = time.perf_counter()
            try:
                conn.execute(
                    "INSERT INTO tasks (name, status, data, created_at) VALUES (?, ?, ?, ?)",
                    (f"thread_{thread_id}_task_{i}", "pending", os.urandom(100), time.time())
                )
                conn.commit()
                wait_time = time.perf_counter() - start
                local_lock_waits.append(wait_time)
            except sqlite3.OperationalError as e:
                errors.append(str(e))
        
        conn.close()
        return local_lock_waits
    
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(writer, i) for i in range(num_threads)]
        for future in as_completed(futures):
            lock_waits.extend(future.result())
    elapsed = time.perf_counter() - start
    
    total_writes = num_threads * writes_per_thread
    return {
        "threads": num_threads,
        "writes_per_thread": writes_per_thread,
        "total_writes": total_writes,
        "elapsed_s": round(elapsed, 4),
        "writes_per_s": round(total_writes / elapsed, 2),
        "lock_wait_ms": {
            "mean": round(statistics.mean(lock_waits) * 1000, 3),
            "median": round(statistics.median(lock_waits) * 1000, 3),
            "p95": round(sorted(lock_waits)[int(len(lock_waits) * 0.95)] * 1000, 3),
            "p99": round(sorted(lock_waits)[int(len(lock_waits) * 0.99)] * 1000, 3),
            "max": round(max(lock_waits) * 1000, 3)
        },
        "errors": len(errors)
    }

def measure_read_write_contention(db_path, mode_name, num_readers=4, num_writers=4, duration_s=5):
    """Measure mixed read/write workload"""
    stop_event = threading.Event()
    read_counts = []
    write_counts = []
    read_latencies = []
    write_latencies = []
    
    def reader():
        conn = sqlite3.connect(db_path, timeout=30)
        count = 0
        latencies = []
        while not stop_event.is_set():
            start = time.perf_counter()
            try:
                list(conn.execute("SELECT * FROM tasks WHERE status = 'pending' LIMIT 100"))
                latencies.append(time.perf_counter() - start)
                count += 1
            except:
                pass
        conn.close()
        return count, latencies
    
    def writer():
        conn = sqlite3.connect(db_path, timeout=30)
        conn.execute("PRAGMA busy_timeout = 30000")
        count = 0
        latencies = []
        while not stop_event.is_set():
            start = time.perf_counter()
            try:
                conn.execute(
                    "INSERT INTO tasks (name, status, data, created_at) VALUES (?, ?, ?, ?)",
                    (f"rw_task_{count}", "pending", os.urandom(100), time.time())
                )
                conn.commit()
                latencies.append(time.perf_counter() - start)
                count += 1
            except:
                pass
        conn.close()
        return count, latencies
    
    with ThreadPoolExecutor(max_workers=num_readers + num_writers) as executor:
        reader_futures = [executor.submit(reader) for _ in range(num_readers)]
        writer_futures = [executor.submit(writer) for _ in range(num_writers)]
        
        time.sleep(duration_s)
        stop_event.set()
        
        for f in reader_futures:
            c, l = f.result()
            read_counts.append(c)
            read_latencies.extend(l)
        for f in writer_futures:
            c, l = f.result()
            write_counts.append(c)
            write_latencies.extend(l)
    
    return {
        "duration_s": duration_s,
        "readers": num_readers,
        "writers": num_writers,
        "total_reads": sum(read_counts),
        "total_writes": sum(write_counts),
        "reads_per_s": round(sum(read_counts) / duration_s, 2),
        "writes_per_s": round(sum(write_counts) / duration_s, 2),
        "read_latency_ms": {
            "mean": round(statistics.mean(read_latencies) * 1000, 3) if read_latencies else 0,
            "p95": round(sorted(read_latencies)[int(len(read_latencies) * 0.95)] * 1000, 3) if read_latencies else 0
        },
        "write_latency_ms": {
            "mean": round(statistics.mean(write_latencies) * 1000, 3) if write_latencies else 0,
            "p95": round(sorted(write_latencies)[int(len(write_latencies) * 0.95)] * 1000, 3) if write_latencies else 0
        }
    }

def measure_edge_cases(db_path):
    """Document WAL-specific edge cases"""
    edge_cases = {}
    
    # Checkpoint behavior
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    
    # Get WAL file size before checkpoint
    wal_path = db_path + "-wal"
    wal_size_before = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    
    # Run checkpoint
    start = time.perf_counter()
    result = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    checkpoint_time = time.perf_counter() - start
    
    wal_size_after = os.path.getsize(wal_path) if os.path.exists(wal_path) else 0
    
    edge_cases["checkpoint"] = {
        "wal_size_before_bytes": wal_size_before,
        "wal_size_after_bytes": wal_size_after,
        "checkpoint_time_ms": round(checkpoint_time * 1000, 3),
        "checkpoint_result": {
            "blocked": result[0],
            "log_frames": result[1],
            "checkpointed_frames": result[2]
        }
    }
    
    # Disk usage comparison
    db_size = os.path.getsize(db_path)
    shm_path = db_path + "-shm"
    shm_size = os.path.getsize(shm_path) if os.path.exists(shm_path) else 0
    
    edge_cases["disk_usage"] = {
        "main_db_bytes": db_size,
        "wal_bytes": wal_size_after,
        "shm_bytes": shm_size,
        "total_bytes": db_size + wal_size_after + shm_size,
        "note": "WAL mode uses additional -wal and -shm files"
    }
    
    conn.close()
    return edge_cases

def run_benchmark_for_mode(mode, db_path):
    """Run all benchmarks for a specific journal mode"""
    conn = sqlite3.connect(db_path)
    conn.execute(f"PRAGMA journal_mode={mode}")
    create_schema(conn)
    conn.close()
    
    print(f"\n{'='*50}")
    print(f"Benchmarking {mode.upper()} mode")
    print(f"{'='*50}")
    
    results = {}
    
    print("  Single writer benchmark...")
    results["single_writer"] = measure_single_writer(db_path, mode)
    print(f"    {results['single_writer']['writes_per_s']} writes/s")
    
    print("  Concurrent writers benchmark (8 threads)...")
    results["concurrent_writers"] = measure_concurrent_writers(db_path, mode)
    print(f"    {results['concurrent_writers']['writes_per_s']} writes/s")
    print(f"    Lock wait p95: {results['concurrent_writers']['lock_wait_ms']['p95']}ms")
    
    print("  Read/write contention benchmark (5s)...")
    results["read_write_contention"] = measure_read_write_contention(db_path, mode)
    print(f"    {results['read_write_contention']['reads_per_s']} reads/s")
    print(f"    {results['read_write_contention']['writes_per_s']} writes/s")
    
    return results

def main():
    print("SQLite WAL vs DELETE Mode Benchmark")
    print("=" * 50)
    
    # Create temp directory for test databases
    with tempfile.TemporaryDirectory() as tmpdir:
        # Benchmark DELETE mode
        delete_db = os.path.join(tmpdir, "delete_mode.db")
        RESULTS["delete_mode"] = run_benchmark_for_mode("DELETE", delete_db)
        
        # Benchmark WAL mode
        wal_db = os.path.join(tmpdir, "wal_mode.db")
        RESULTS["wal_mode"] = run_benchmark_for_mode("WAL", wal_db)
        
        # Measure edge cases on WAL db
        print("\nMeasuring WAL edge cases...")
        RESULTS["edge_cases"] = measure_edge_cases(wal_db)
    
    # Calculate improvements
    delete_concurrent = RESULTS["delete_mode"]["concurrent_writers"]["writes_per_s"]
    wal_concurrent = RESULTS["wal_mode"]["concurrent_writers"]["writes_per_s"]
    
    delete_rw_writes = RESULTS["delete_mode"]["read_write_contention"]["writes_per_s"]
    wal_rw_writes = RESULTS["wal_mode"]["read_write_contention"]["writes_per_s"]
    
    delete_lock_p95 = RESULTS["delete_mode"]["concurrent_writers"]["lock_wait_ms"]["p95"]
    wal_lock_p95 = RESULTS["wal_mode"]["concurrent_writers"]["lock_wait_ms"]["p95"]
    
    RESULTS["comparison"] = {
        "concurrent_writes_improvement_pct": round((wal_concurrent / delete_concurrent - 1) * 100, 2),
        "mixed_workload_writes_improvement_pct": round((wal_rw_writes / delete_rw_writes - 1) * 100, 2),
        "lock_wait_p95_reduction_pct": round((1 - wal_lock_p95 / delete_lock_p95) * 100, 2) if delete_lock_p95 > 0 else 0
    }
    
    RESULTS["summary"] = {
        "recommendation": "Enable WAL mode for control-plane database",
        "key_benefits": [
            f"Concurrent write throughput: +{RESULTS['comparison']['concurrent_writes_improvement_pct']}%",
            f"Mixed workload writes: +{RESULTS['comparison']['mixed_workload_writes_improvement_pct']}%",
            f"Lock wait time (p95): -{RESULTS['comparison']['lock_wait_p95_reduction_pct']}%"
        ],
        "caveats": [
            "WAL files can grow large without periodic checkpointing",
            "Additional disk space for -wal and -shm files",
            "Checkpoint operations can briefly block writers",
            "Not suitable for network filesystems (NFS)"
        ],
        "implementation": "PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;"
    }
    
    print("\n" + "=" * 50)
    print("SUMMARY")
    print("=" * 50)
    print(f"Concurrent writes improvement: {RESULTS['comparison']['concurrent_writes_improvement_pct']}%")
    print(f"Mixed workload improvement: {RESULTS['comparison']['mixed_workload_writes_improvement_pct']}%")
    print(f"Lock wait reduction (p95): {RESULTS['comparison']['lock_wait_p95_reduction_pct']}%")
    
    return RESULTS

if __name__ == "__main__":
    results = main()
    
    # Save results
    output_path = "/home/ubuntu/.openclaw/workspace/control-plane/workspaces/bench/artifacts/sqlite_wal_benchmark.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to: {output_path}")
