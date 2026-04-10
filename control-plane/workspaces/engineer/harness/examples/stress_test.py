#!/usr/bin/env python3
"""
Example: Using baseline harness for stress testing.

Demonstrates integration with the fenris-style stress test pattern.
"""

import subprocess
import time
from harness import BaselineHarness, ExperimentConfig, get_system_metrics


def run_cpu_stress(workers: int, duration_sec: int) -> dict:
    """Run CPU stress test."""
    start_metrics = get_system_metrics()
    
    # Simulate CPU-bound work
    import multiprocessing
    def cpu_work():
        end_time = time.time() + duration_sec
        x = 0
        while time.time() < end_time:
            x += sum(i*i for i in range(1000))
    
    workers = min(workers, multiprocessing.cpu_count())
    processes = []
    start = time.time()
    
    for _ in range(workers):
        p = multiprocessing.Process(target=cpu_work)
        p.start()
        processes.append(p)
    
    for p in processes:
        p.join()
    
    duration = time.time() - start
    end_metrics = get_system_metrics()
    
    return {
        "workers": workers,
        "duration_sec": round(duration, 2),
        "start_load": start_metrics.get("load_1m", 0),
        "end_load": end_metrics.get("load_1m", 0),
    }


def run_memory_pressure(target_mb: int) -> dict:
    """Run memory pressure test."""
    start_metrics = get_system_metrics()
    
    # Allocate memory
    chunks = []
    allocated = 0
    chunk_size = 10 * 1024 * 1024  # 10MB chunks
    
    try:
        while allocated < target_mb * 1024 * 1024:
            chunks.append(bytearray(chunk_size))
            allocated += chunk_size
    except MemoryError:
        pass
    
    end_metrics = get_system_metrics()
    
    # Release
    del chunks
    
    return {
        "target_mb": target_mb,
        "allocated_mb": allocated // (1024 * 1024),
        "start_mem_percent": start_metrics.get("mem_used_percent", 0),
        "peak_mem_percent": end_metrics.get("mem_used_percent", 0),
    }


def main():
    config = ExperimentConfig(
        experiment_id="stress_test_example",
        name="harness-stress-demo",
        lane="engineer",
        tags=["stress-test", "example", "harness-demo"],
        parameters={
            "cpu_workers": 4,
            "cpu_duration_sec": 2,
            "memory_target_mb": 100,
        }
    )
    
    with BaselineHarness(config) as harness:
        print("Starting stress test demo...")
        harness.checkpoint("test_start")
        
        # Phase 1: CPU stress
        print("Phase 1: CPU stress...")
        harness.checkpoint("cpu_stress_start")
        cpu_results = run_cpu_stress(
            workers=config.parameters["cpu_workers"],
            duration_sec=config.parameters["cpu_duration_sec"]
        )
        harness.log_metric("cpu_stress_duration", cpu_results["duration_sec"])
        harness.log_metric("cpu_stress_peak_load", cpu_results["end_load"])
        harness.checkpoint("cpu_stress_complete", cpu_results)
        print(f"  CPU: {cpu_results}")
        
        # Phase 2: Memory pressure
        print("Phase 2: Memory pressure...")
        harness.checkpoint("memory_pressure_start")
        mem_results = run_memory_pressure(
            target_mb=config.parameters["memory_target_mb"]
        )
        harness.log_metric("memory_allocated_mb", mem_results["allocated_mb"])
        harness.log_metric("memory_peak_percent", mem_results["peak_mem_percent"])
        harness.checkpoint("memory_pressure_complete", mem_results)
        print(f"  Memory: {mem_results}")
        
        # Save combined results
        harness.save_artifact("stress_summary", {
            "cpu": cpu_results,
            "memory": mem_results,
            "result": "PASS"
        })
        
        harness.checkpoint("test_complete")
        print(f"\nTest complete. Artifacts: {harness.artifacts_dir}")


if __name__ == "__main__":
    main()
