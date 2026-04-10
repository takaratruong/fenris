#!/usr/bin/env python3
"""
Method 1 Evaluation - Baseline C

Method 1: Sequential Processing Approach
- Processes items sequentially with in-order guarantees
- Lower memory footprint, predictable execution
- Establishes baseline metrics for comparison with Methods 2 & 3
"""

import sys
import time
import random
import hashlib
from pathlib import Path

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from baseline_c_harness import BaselineCHarness, atomic_json_read

# Shared parameters from branch state
BRANCH_STATE = Path(__file__).parent.parent / "branch_state.json"


def load_shared_parameters():
    """Load common parameters from branch state."""
    state = atomic_json_read(BRANCH_STATE)
    return state.get("shared_context", {}).get("common_parameters", {
        "input_size": 1000,
        "iterations": 10,
        "seed": 42
    })


def generate_workload(size: int, seed: int) -> list:
    """Generate reproducible test workload."""
    random.seed(seed)
    return [random.randint(1, 10000) for _ in range(size)]


def method_1_process_item(item: int) -> dict:
    """
    Method 1: Sequential hash computation with validation.
    
    Characteristics:
    - Simple, predictable processing
    - O(1) memory per item
    - Establishes baseline timing
    """
    # Compute hash
    hash_input = str(item).encode()
    hash_result = hashlib.sha256(hash_input).hexdigest()
    
    # Simulate computational work
    validation = sum(int(c, 16) for c in hash_result[:8])
    
    return {
        "input": item,
        "hash": hash_result[:16],
        "validation_sum": validation,
        "passes": validation % 2 == 0
    }


def run_method_1_evaluation(harness: BaselineCHarness, params: dict) -> dict:
    """Execute Method 1 evaluation."""
    
    harness.checkpoint("workload_generation_start")
    workload = generate_workload(params["input_size"], params["seed"])
    harness.checkpoint("workload_generation_complete", {"items": len(workload)})
    
    # Track results across iterations
    all_results = []
    timing_data = []
    
    for iteration in range(params["iterations"]):
        iter_start = time.time()
        harness.checkpoint(f"iteration_{iteration}_start")
        
        # Sequential processing (Method 1's core approach)
        iter_results = []
        pass_count = 0
        validation_sum = 0
        
        for item in workload:
            result = method_1_process_item(item)
            iter_results.append(result)
            if result["passes"]:
                pass_count += 1
            validation_sum += result["validation_sum"]
        
        iter_duration = time.time() - iter_start
        timing_data.append(iter_duration)
        
        # Log iteration metrics
        harness.log_metric("iteration_duration_sec", iter_duration, {"iteration": str(iteration)})
        harness.log_metric("pass_rate", pass_count / len(workload), {"iteration": str(iteration)})
        harness.log_metric("validation_sum", validation_sum, {"iteration": str(iteration)})
        
        all_results.append({
            "iteration": iteration,
            "pass_count": pass_count,
            "pass_rate": pass_count / len(workload),
            "validation_sum": validation_sum,
            "duration_sec": iter_duration
        })
        
        harness.checkpoint(f"iteration_{iteration}_complete")
    
    # Compute aggregate metrics
    avg_duration = sum(timing_data) / len(timing_data)
    min_duration = min(timing_data)
    max_duration = max(timing_data)
    avg_pass_rate = sum(r["pass_rate"] for r in all_results) / len(all_results)
    
    harness.log_metric("avg_iteration_duration_sec", avg_duration)
    harness.log_metric("min_iteration_duration_sec", min_duration)
    harness.log_metric("max_iteration_duration_sec", max_duration)
    harness.log_metric("overall_pass_rate", avg_pass_rate)
    harness.log_metric("total_items_processed", params["input_size"] * params["iterations"])
    harness.log_metric("throughput_items_per_sec", params["input_size"] / avg_duration)
    
    return {
        "method": "method_1",
        "approach": "sequential_processing",
        "characteristics": {
            "parallelism": "none",
            "memory_model": "constant",
            "ordering_guarantee": "in_order"
        },
        "iterations": all_results,
        "aggregate_metrics": {
            "avg_duration_sec": avg_duration,
            "min_duration_sec": min_duration,
            "max_duration_sec": max_duration,
            "std_dev_duration": (sum((t - avg_duration) ** 2 for t in timing_data) / len(timing_data)) ** 0.5,
            "overall_pass_rate": avg_pass_rate,
            "total_items": params["input_size"] * params["iterations"],
            "throughput_items_per_sec": params["input_size"] / avg_duration
        }
    }


def main():
    """Run Method 1 evaluation."""
    task_id = "tsk_d68dfd7388a7"
    
    print(f"Starting Method 1 Evaluation (Task: {task_id})")
    print("=" * 50)
    
    params = load_shared_parameters()
    print(f"Parameters: {params}")
    
    with BaselineCHarness(method_id="method_1", task_id=task_id) as harness:
        # Check for results from other methods (shared context)
        other_results = harness.get_other_method_results()
        if other_results:
            print(f"Found results from other methods: {list(other_results.keys())}")
            harness.checkpoint("found_prior_method_results", {"methods": list(other_results.keys())})
        
        # Run the evaluation
        harness.checkpoint("evaluation_start")
        results = run_method_1_evaluation(harness, params)
        harness.checkpoint("evaluation_complete")
        
        # Save detailed results
        harness.save_artifact("detailed_results", results)
        
        # Contribute to shared context for other methods
        harness.update_shared_context({
            "method_1_completed": True,
            "method_1_throughput": results["aggregate_metrics"]["throughput_items_per_sec"],
            "method_1_avg_duration": results["aggregate_metrics"]["avg_duration_sec"],
            "method_1_pass_rate": results["aggregate_metrics"]["overall_pass_rate"],
            "method_1_approach": "sequential",
            "method_1_characteristics": results["characteristics"]
        })
        
        # Add a cross-method signal
        harness.add_cross_method_signal("baseline_established", {
            "baseline_throughput": results["aggregate_metrics"]["throughput_items_per_sec"],
            "baseline_approach": "sequential"
        })
        
        print("\nEvaluation Complete")
        print("-" * 50)
        print(f"Throughput: {results['aggregate_metrics']['throughput_items_per_sec']:.2f} items/sec")
        print(f"Avg Duration: {results['aggregate_metrics']['avg_duration_sec']:.4f} sec")
        print(f"Pass Rate: {results['aggregate_metrics']['overall_pass_rate']:.4f}")
        print(f"Artifacts: {harness.artifacts_dir}")
    
    return results


if __name__ == "__main__":
    results = main()
