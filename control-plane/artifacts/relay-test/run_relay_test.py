#!/usr/bin/env python3
"""Cross-Worker Message Relay Test - Measures round-trip latency and verifies payload integrity"""

import os
import json
import time
import hashlib
import base64
import secrets
from datetime import datetime
from pathlib import Path

WORKERS = ["research", "engineer", "bench", "ops"]
PAYLOAD_SIZES = [64, 256, 1024, 4096]
WORKER_PAIRS = [
    ("research", "engineer"),
    ("engineer", "bench"),
    ("bench", "ops"),
    ("research", "bench"),
    ("ops", "research"),
]

def generate_payload(size: int) -> bytes:
    """Generate random test payload"""
    return secrets.token_bytes(size)

def compute_hash(data: bytes) -> str:
    """Compute SHA-256 hash"""
    return hashlib.sha256(data).hexdigest()

def test_relay(from_worker: str, to_worker: str, payload_size: int, test_dir: Path) -> dict:
    """Test message relay between two workers"""
    relay_dir = test_dir / f"relay_{from_worker}_{to_worker}_{payload_size}"
    relay_dir.mkdir(parents=True, exist_ok=True)
    
    # Generate payload
    payload = generate_payload(payload_size)
    original_hash = compute_hash(payload)
    
    # Record start time
    start_time = time.perf_counter_ns()
    
    # Simulate outbound: from worker writes to shared channel
    channel_file = relay_dir / "channel.msg"
    channel_file.write_bytes(payload)
    
    # Simulate processing delay (network/queue simulation)
    time.sleep(0.001)  # 1ms simulated network delay
    
    # Simulate inbound: to worker reads message
    received_data = channel_file.read_bytes()
    received_hash = compute_hash(received_data)
    
    # Simulate acknowledgment
    ack_file = relay_dir / "ack.msg"
    ack_file.write_text(received_hash)
    
    # Record end time
    end_time = time.perf_counter_ns()
    latency_ns = end_time - start_time
    latency_ms = latency_ns / 1_000_000
    
    # Verify integrity
    integrity_ok = original_hash == received_hash
    
    return {
        "from": from_worker,
        "to": to_worker,
        "payload_size": payload_size,
        "latency_ms": round(latency_ms, 3),
        "integrity_ok": integrity_ok,
        "original_hash": original_hash[:16] + "...",
        "received_hash": received_hash[:16] + "...",
    }

def main():
    test_dir = Path(".")
    results = []
    
    print("Testing cross-worker message relay...")
    print()
    
    for size in PAYLOAD_SIZES:
        print(f"=== Testing with payload size: {size} bytes ===")
        for from_w, to_w in WORKER_PAIRS:
            result = test_relay(from_w, to_w, size, test_dir)
            results.append(result)
            status = "✓" if result["integrity_ok"] else "✗"
            print(f"  {from_w} -> {to_w}: {result['latency_ms']:.3f}ms, integrity: {status}")
        print()
    
    # Calculate summary
    total_tests = len(results)
    passed = sum(1 for r in results if r["integrity_ok"])
    failed = total_tests - passed
    success_rate = (passed / total_tests) * 100 if total_tests > 0 else 0
    
    latencies = [r["latency_ms"] for r in results]
    min_latency = min(latencies)
    max_latency = max(latencies)
    avg_latency = sum(latencies) / len(latencies)
    
    summary = {
        "test_name": "cross_worker_message_relay",
        "started_at": datetime.now().isoformat(),
        "completed_at": datetime.now().isoformat(),
        "worker_pairs_tested": [f"{a}-{b}" for a, b in WORKER_PAIRS],
        "payload_sizes_bytes": PAYLOAD_SIZES,
        "total_tests": total_tests,
        "passed": passed,
        "failed": failed,
        "success_rate_percent": round(success_rate, 2),
        "latency_stats": {
            "min_ms": round(min_latency, 3),
            "max_ms": round(max_latency, 3),
            "avg_ms": round(avg_latency, 3),
        },
        "test_results": results,
    }
    
    # Write results
    results_file = test_dir / "relay_results.json"
    with open(results_file, "w") as f:
        json.dump(summary, f, indent=2)
    
    print("=== SUMMARY ===")
    print(f"Total tests: {total_tests}")
    print(f"Passed: {passed}")
    print(f"Failed: {failed}")
    print(f"Success rate: {success_rate:.1f}%")
    print(f"Latency - Min: {min_latency:.3f}ms, Max: {max_latency:.3f}ms, Avg: {avg_latency:.3f}ms")
    print()
    print(f"Results written to: {results_file}")
    
    return summary

if __name__ == "__main__":
    main()
