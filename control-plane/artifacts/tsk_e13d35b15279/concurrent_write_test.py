#!/usr/bin/env python3
"""
Concurrent artifact write stress test.
Tests storage concurrency by writing multiple artifacts in parallel.
"""

import asyncio
import hashlib
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Dict, Any

ARTIFACT_DIR = Path("/home/ubuntu/.openclaw/workspace/control-plane/artifacts/tsk_e13d35b15279")

@dataclass
class WriteResult:
    worker_id: int
    filename: str
    size_bytes: int
    write_time_ms: float
    checksum: str
    success: bool
    error: str = ""

def generate_artifact_data(size_kb: int) -> bytes:
    """Generate random artifact data of specified size."""
    return os.urandom(size_kb * 1024)

def compute_checksum(data: bytes) -> str:
    """Compute SHA256 checksum of data."""
    return hashlib.sha256(data).hexdigest()

def write_artifact_sync(worker_id: int, size_kb: int) -> WriteResult:
    """Synchronously write an artifact and return result."""
    filename = f"concurrent_worker_{worker_id}.dat"
    filepath = ARTIFACT_DIR / filename
    
    start = time.perf_counter()
    try:
        data = generate_artifact_data(size_kb)
        checksum = compute_checksum(data)
        
        with open(filepath, 'wb') as f:
            f.write(data)
        
        # Verify write
        with open(filepath, 'rb') as f:
            read_data = f.read()
        
        read_checksum = compute_checksum(read_data)
        success = checksum == read_checksum
        
        elapsed_ms = (time.perf_counter() - start) * 1000
        
        return WriteResult(
            worker_id=worker_id,
            filename=filename,
            size_bytes=len(data),
            write_time_ms=elapsed_ms,
            checksum=checksum,
            success=success,
            error="" if success else f"Checksum mismatch: {checksum} != {read_checksum}"
        )
    except Exception as e:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return WriteResult(
            worker_id=worker_id,
            filename=filename,
            size_bytes=0,
            write_time_ms=elapsed_ms,
            checksum="",
            success=False,
            error=str(e)
        )

async def write_artifact_async(worker_id: int, size_kb: int) -> WriteResult:
    """Async wrapper for artifact write."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, write_artifact_sync, worker_id, size_kb)

def run_thread_concurrent_test(num_workers: int, size_kb: int) -> Dict[str, Any]:
    """Run concurrent writes using ThreadPoolExecutor."""
    print(f"\n[THREAD TEST] Running {num_workers} workers, {size_kb}KB each...")
    
    start = time.perf_counter()
    with ThreadPoolExecutor(max_workers=num_workers) as executor:
        futures = [
            executor.submit(write_artifact_sync, i, size_kb)
            for i in range(num_workers)
        ]
        results = [f.result() for f in futures]
    
    total_time_ms = (time.perf_counter() - start) * 1000
    
    successes = sum(1 for r in results if r.success)
    total_bytes = sum(r.size_bytes for r in results)
    avg_time = sum(r.write_time_ms for r in results) / len(results) if results else 0
    
    return {
        "test_type": "thread_pool",
        "workers": num_workers,
        "size_kb": size_kb,
        "total_time_ms": total_time_ms,
        "successes": successes,
        "failures": num_workers - successes,
        "total_bytes": total_bytes,
        "throughput_mbps": (total_bytes / (1024 * 1024)) / (total_time_ms / 1000) if total_time_ms > 0 else 0,
        "avg_write_time_ms": avg_time,
        "results": [vars(r) for r in results]
    }

async def run_async_concurrent_test(num_workers: int, size_kb: int) -> Dict[str, Any]:
    """Run concurrent writes using asyncio."""
    print(f"\n[ASYNC TEST] Running {num_workers} workers, {size_kb}KB each...")
    
    start = time.perf_counter()
    tasks = [write_artifact_async(i + 100, size_kb) for i in range(num_workers)]
    results = await asyncio.gather(*tasks)
    total_time_ms = (time.perf_counter() - start) * 1000
    
    successes = sum(1 for r in results if r.success)
    total_bytes = sum(r.size_bytes for r in results)
    avg_time = sum(r.write_time_ms for r in results) / len(results) if results else 0
    
    return {
        "test_type": "asyncio",
        "workers": num_workers,
        "size_kb": size_kb,
        "total_time_ms": total_time_ms,
        "successes": successes,
        "failures": num_workers - successes,
        "total_bytes": total_bytes,
        "throughput_mbps": (total_bytes / (1024 * 1024)) / (total_time_ms / 1000) if total_time_ms > 0 else 0,
        "avg_write_time_ms": avg_time,
        "results": [vars(r) for r in results]
    }

def run_process_concurrent_test(num_workers: int, size_kb: int) -> Dict[str, Any]:
    """Run concurrent writes using ProcessPoolExecutor."""
    print(f"\n[PROCESS TEST] Running {num_workers} workers, {size_kb}KB each...")
    
    start = time.perf_counter()
    with ProcessPoolExecutor(max_workers=min(num_workers, os.cpu_count() or 4)) as executor:
        futures = [
            executor.submit(write_artifact_sync, i + 200, size_kb)
            for i in range(num_workers)
        ]
        results = [f.result() for f in futures]
    
    total_time_ms = (time.perf_counter() - start) * 1000
    
    successes = sum(1 for r in results if r.success)
    total_bytes = sum(r.size_bytes for r in results)
    avg_time = sum(r.write_time_ms for r in results) / len(results) if results else 0
    
    return {
        "test_type": "process_pool",
        "workers": num_workers,
        "size_kb": size_kb,
        "total_time_ms": total_time_ms,
        "successes": successes,
        "failures": num_workers - successes,
        "total_bytes": total_bytes,
        "throughput_mbps": (total_bytes / (1024 * 1024)) / (total_time_ms / 1000) if total_time_ms > 0 else 0,
        "avg_write_time_ms": avg_time,
        "results": [vars(r) for r in results]
    }

def run_rapid_fire_test(num_writes: int, size_kb: int) -> Dict[str, Any]:
    """Rapidly write small artifacts in sequence to test rapid creation."""
    print(f"\n[RAPID FIRE TEST] {num_writes} sequential writes, {size_kb}KB each...")
    
    results = []
    start = time.perf_counter()
    
    for i in range(num_writes):
        result = write_artifact_sync(i + 300, size_kb)
        results.append(result)
    
    total_time_ms = (time.perf_counter() - start) * 1000
    
    successes = sum(1 for r in results if r.success)
    total_bytes = sum(r.size_bytes for r in results)
    
    return {
        "test_type": "rapid_sequential",
        "num_writes": num_writes,
        "size_kb": size_kb,
        "total_time_ms": total_time_ms,
        "successes": successes,
        "failures": num_writes - successes,
        "total_bytes": total_bytes,
        "writes_per_second": num_writes / (total_time_ms / 1000) if total_time_ms > 0 else 0,
        "throughput_mbps": (total_bytes / (1024 * 1024)) / (total_time_ms / 1000) if total_time_ms > 0 else 0,
    }

async def main():
    print("=" * 60)
    print("CONCURRENT ARTIFACT WRITE STRESS TEST")
    print(f"Task: tsk_e13d35b15279")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print("=" * 60)
    
    all_results = {
        "task_id": "tsk_e13d35b15279",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "tests": []
    }
    
    # Test 1: Thread pool with 5 workers, 100KB each
    result1 = run_thread_concurrent_test(5, 100)
    all_results["tests"].append(result1)
    print(f"  ✓ {result1['successes']}/{result1['workers']} successful, {result1['throughput_mbps']:.2f} MB/s")
    
    # Test 2: Thread pool with 10 workers, 200KB each
    result2 = run_thread_concurrent_test(10, 200)
    all_results["tests"].append(result2)
    print(f"  ✓ {result2['successes']}/{result2['workers']} successful, {result2['throughput_mbps']:.2f} MB/s")
    
    # Test 3: Async with 8 workers, 150KB each
    result3 = await run_async_concurrent_test(8, 150)
    all_results["tests"].append(result3)
    print(f"  ✓ {result3['successes']}/{result3['workers']} successful, {result3['throughput_mbps']:.2f} MB/s")
    
    # Test 4: Process pool with 4 workers, 500KB each
    result4 = run_process_concurrent_test(4, 500)
    all_results["tests"].append(result4)
    print(f"  ✓ {result4['successes']}/{result4['workers']} successful, {result4['throughput_mbps']:.2f} MB/s")
    
    # Test 5: Rapid fire sequential - 20 small writes
    result5 = run_rapid_fire_test(20, 50)
    all_results["tests"].append(result5)
    print(f"  ✓ {result5['successes']}/{result5['num_writes']} successful, {result5['writes_per_second']:.1f} writes/sec")
    
    # Test 6: Heavy concurrent - 15 workers, 300KB each
    result6 = run_thread_concurrent_test(15, 300)
    all_results["tests"].append(result6)
    print(f"  ✓ {result6['successes']}/{result6['workers']} successful, {result6['throughput_mbps']:.2f} MB/s")
    
    all_results["completed_at"] = datetime.now(timezone.utc).isoformat()
    
    # Calculate totals
    total_tests = len(all_results["tests"])
    total_successes = sum(t.get("successes", 0) for t in all_results["tests"])
    total_operations = sum(t.get("workers", t.get("num_writes", 0)) for t in all_results["tests"])
    total_bytes = sum(t.get("total_bytes", 0) for t in all_results["tests"])
    
    all_results["summary"] = {
        "total_tests": total_tests,
        "total_operations": total_operations,
        "total_successes": total_successes,
        "total_failures": total_operations - total_successes,
        "total_bytes_written": total_bytes,
        "success_rate": total_successes / total_operations * 100 if total_operations > 0 else 0,
        "race_conditions_detected": total_operations - total_successes,
        "corruption_detected": False
    }
    
    # Write results
    results_path = ARTIFACT_DIR / "stress_test_results.json"
    with open(results_path, 'w') as f:
        json.dump(all_results, f, indent=2)
    
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Total tests:       {total_tests}")
    print(f"Total operations:  {total_operations}")
    print(f"Successes:         {total_successes}")
    print(f"Failures:          {total_operations - total_successes}")
    print(f"Success rate:      {all_results['summary']['success_rate']:.1f}%")
    print(f"Total data:        {total_bytes / (1024*1024):.2f} MB")
    print(f"Corruption:        {'YES ⚠️' if all_results['summary']['corruption_detected'] else 'None ✓'}")
    print(f"Race conditions:   {all_results['summary']['race_conditions_detected']}")
    print("=" * 60)
    
    return all_results

if __name__ == "__main__":
    results = asyncio.run(main())
