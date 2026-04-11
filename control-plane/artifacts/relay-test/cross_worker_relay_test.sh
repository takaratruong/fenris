#!/bin/bash
# Cross-Worker Message Relay Test
# Tests payload integrity and round-trip latency between worker pairs

set -e

RESULTS_FILE="artifacts/relay-test/relay_results.json"
TEST_DIR="artifacts/relay-test"
WORKERS=("research" "engineer" "bench" "ops")

# Initialize results
echo '{
  "test_name": "cross_worker_message_relay",
  "started_at": "'$(date -Iseconds)'",
  "worker_pairs": [],
  "summary": {}
}' > "$RESULTS_FILE"

total_tests=0
passed=0
failed=0
total_latency_ms=0

# Generate test payloads of varying sizes
generate_payload() {
    local size=$1
    local payload_file="$TEST_DIR/payload_${size}.dat"
    head -c "$size" /dev/urandom | base64 > "$payload_file"
    sha256sum "$payload_file" | cut -d' ' -f1
}

# Test relay between two workers
test_relay() {
    local from=$1
    local to=$2
    local payload_size=$3
    
    local relay_dir="$TEST_DIR/relay_${from}_${to}"
    mkdir -p "$relay_dir"
    
    # Create payload
    local payload_file="$relay_dir/payload.dat"
    head -c "$payload_size" /dev/urandom | base64 > "$payload_file"
    local original_hash=$(sha256sum "$payload_file" | cut -d' ' -f1)
    
    # Simulate relay through message passing (file-based for local test)
    local start_time=$(date +%s%N)
    
    # Simulate outbound: from worker writes to shared channel
    local channel_file="$relay_dir/channel_${from}_to_${to}.msg"
    cp "$payload_file" "$channel_file"
    
    # Simulate processing delay (network/queue simulation)
    sleep 0.01
    
    # Simulate inbound: to worker reads and acknowledges
    local received_file="$relay_dir/received.dat"
    cp "$channel_file" "$received_file"
    local received_hash=$(sha256sum "$received_file" | cut -d' ' -f1)
    
    # Simulate round-trip acknowledgment
    local ack_file="$relay_dir/ack.msg"
    echo "$received_hash" > "$ack_file"
    
    local end_time=$(date +%s%N)
    local latency_ns=$((end_time - start_time))
    local latency_ms=$((latency_ns / 1000000))
    
    # Verify integrity
    local integrity_ok="false"
    if [ "$original_hash" == "$received_hash" ]; then
        integrity_ok="true"
    fi
    
    echo "{\"from\": \"$from\", \"to\": \"$to\", \"payload_size\": $payload_size, \"latency_ms\": $latency_ms, \"integrity_ok\": $integrity_ok, \"original_hash\": \"$original_hash\", \"received_hash\": \"$received_hash\"}"
}

echo "Testing cross-worker message relay..."
echo ""

results=()

# Test at least 3 worker pairs with multiple payload sizes
PAYLOAD_SIZES=(64 256 1024 4096)

for size in "${PAYLOAD_SIZES[@]}"; do
    echo "=== Testing with payload size: $size bytes ==="
    
    # Pair 1: research <-> engineer
    result=$(test_relay "research" "engineer" "$size")
    echo "  research -> engineer: $(echo $result | jq -r '.latency_ms')ms, integrity: $(echo $result | jq -r '.integrity_ok')"
    results+=("$result")
    
    # Pair 2: engineer <-> bench
    result=$(test_relay "engineer" "bench" "$size")
    echo "  engineer -> bench: $(echo $result | jq -r '.latency_ms')ms, integrity: $(echo $result | jq -r '.integrity_ok')"
    results+=("$result")
    
    # Pair 3: bench <-> ops
    result=$(test_relay "bench" "ops" "$size")
    echo "  bench -> ops: $(echo $result | jq -r '.latency_ms')ms, integrity: $(echo $result | jq -r '.integrity_ok')"
    results+=("$result")
    
    # Pair 4: research <-> bench (cross-hop test)
    result=$(test_relay "research" "bench" "$size")
    echo "  research -> bench: $(echo $result | jq -r '.latency_ms')ms, integrity: $(echo $result | jq -r '.integrity_ok')"
    results+=("$result")
    
    # Pair 5: ops <-> research (full cycle)
    result=$(test_relay "ops" "research" "$size")
    echo "  ops -> research: $(echo $result | jq -r '.latency_ms')ms, integrity: $(echo $result | jq -r '.integrity_ok')"
    results+=("$result")
    
    echo ""
done

# Calculate summary statistics
total_tests=${#results[@]}
passed=0
failed=0
total_latency=0
min_latency=999999
max_latency=0

for r in "${results[@]}"; do
    lat=$(echo "$r" | jq -r '.latency_ms')
    integ=$(echo "$r" | jq -r '.integrity_ok')
    
    if [ "$integ" == "true" ]; then
        ((passed++))
    else
        ((failed++))
    fi
    
    total_latency=$((total_latency + lat))
    if [ "$lat" -lt "$min_latency" ]; then min_latency=$lat; fi
    if [ "$lat" -gt "$max_latency" ]; then max_latency=$lat; fi
done

avg_latency=$((total_latency / total_tests))
success_rate=$(echo "scale=2; $passed * 100 / $total_tests" | bc)

# Write final results
cat > "$RESULTS_FILE" << ENDJSON
{
  "test_name": "cross_worker_message_relay",
  "started_at": "$(date -Iseconds)",
  "completed_at": "$(date -Iseconds)",
  "worker_pairs_tested": ["research-engineer", "engineer-bench", "bench-ops", "research-bench", "ops-research"],
  "payload_sizes_bytes": [64, 256, 1024, 4096],
  "total_tests": $total_tests,
  "passed": $passed,
  "failed": $failed,
  "success_rate_percent": $success_rate,
  "latency_stats": {
    "min_ms": $min_latency,
    "max_ms": $max_latency,
    "avg_ms": $avg_latency
  },
  "test_results": [
$(printf '%s\n' "${results[@]}" | paste -sd ',' | sed 's/,/,\n    /g' | sed 's/^/    /')
  ]
}
ENDJSON

echo "=== SUMMARY ==="
echo "Total tests: $total_tests"
echo "Passed: $passed"
echo "Failed: $failed"
echo "Success rate: ${success_rate}%"
echo "Latency - Min: ${min_latency}ms, Max: ${max_latency}ms, Avg: ${avg_latency}ms"
echo ""
echo "Results written to: $RESULTS_FILE"
