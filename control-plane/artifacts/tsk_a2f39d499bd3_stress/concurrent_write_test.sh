#!/bin/bash
# Concurrent artifact write stress test
# 5 workers writing distinct artifacts simultaneously

set -e

ARTIFACT_DIR="/home/ubuntu/.openclaw/workspace/control-plane/artifacts/tsk_a2f39d499bd3_stress"
DB_PATH="/home/ubuntu/.openclaw/workspace/control-plane/control_plane.db"
TASK_ID="tsk_a2f39d499bd3"
THREAD_ID="thr_74e02923a9ec"
RESULTS_FILE="$ARTIFACT_DIR/stress_test_results.json"

mkdir -p "$ARTIFACT_DIR"

# Generate unique content for each worker
generate_artifact() {
    local worker_id=$1
    local size_kb=$2
    local artifact_id="art_stress_w${worker_id}_$(date +%s%N)"
    local filename="worker_${worker_id}_artifact.dat"
    local filepath="$ARTIFACT_DIR/$filename"
    
    # Generate random binary data with embedded checksum marker
    head -c $((size_kb * 1024)) /dev/urandom > "$filepath.tmp"
    
    # Calculate checksum before write completes
    local checksum=$(sha256sum "$filepath.tmp" | cut -d' ' -f1)
    
    # Atomic move
    mv "$filepath.tmp" "$filepath"
    
    # Register in database with retry logic for concurrent access
    local retry=0
    while [ $retry -lt 5 ]; do
        sqlite3 "$DB_PATH" "
            INSERT INTO artifacts (id, task_id, thread_id, name, path, content_hash, created_by)
            VALUES ('$artifact_id', '$TASK_ID', '$THREAD_ID', 'stress_test_worker_$worker_id', '$filepath', '$checksum', 'worker_$worker_id');
        " 2>/dev/null && break
        retry=$((retry + 1))
        sleep 0.$((RANDOM % 5))
    done
    
    echo "{\"worker\": $worker_id, \"artifact_id\": \"$artifact_id\", \"path\": \"$filepath\", \"checksum\": \"$checksum\", \"size_kb\": $size_kb}"
}

# Verify artifact integrity
verify_artifact() {
    local filepath=$1
    local expected_checksum=$2
    
    if [ ! -f "$filepath" ]; then
        echo "MISSING"
        return 1
    fi
    
    local actual_checksum=$(sha256sum "$filepath" | cut -d' ' -f1)
    if [ "$actual_checksum" = "$expected_checksum" ]; then
        echo "OK"
        return 0
    else
        echo "CORRUPTED"
        return 1
    fi
}

echo "Starting concurrent write stress test at $(date -Iseconds)"
echo "=============================================="

# Launch 5 workers in parallel with varying artifact sizes
pids=()
for worker in 1 2 3 4 5; do
    size=$((100 + worker * 50))  # 150KB to 350KB
    (generate_artifact $worker $size) > "$ARTIFACT_DIR/worker_${worker}_result.json" &
    pids+=($!)
done

# Wait for all workers to complete
echo "Waiting for ${#pids[@]} workers to complete..."
for pid in "${pids[@]}"; do
    wait $pid
done

echo "All workers completed. Verifying artifacts..."

# Collect results and verify
echo "{" > "$RESULTS_FILE"
echo "  \"test_id\": \"stress_$(date +%s)\"," >> "$RESULTS_FILE"
echo "  \"timestamp\": \"$(date -Iseconds)\"," >> "$RESULTS_FILE"
echo "  \"worker_count\": 5," >> "$RESULTS_FILE"
echo "  \"artifacts\": [" >> "$RESULTS_FILE"

all_valid=true
first=true
for worker in 1 2 3 4 5; do
    result_file="$ARTIFACT_DIR/worker_${worker}_result.json"
    if [ -f "$result_file" ]; then
        result=$(cat "$result_file")
        filepath=$(echo "$result" | grep -o '"path": "[^"]*"' | cut -d'"' -f4)
        checksum=$(echo "$result" | grep -o '"checksum": "[^"]*"' | cut -d'"' -f4)
        
        verification=$(verify_artifact "$filepath" "$checksum")
        
        if [ "$first" = true ]; then
            first=false
        else
            echo "," >> "$RESULTS_FILE"
        fi
        
        # Add verification result to output
        echo "    {\"worker\": $worker, \"verification\": \"$verification\", \"checksum_match\": $([ "$verification" = "OK" ] && echo "true" || echo "false")}" >> "$RESULTS_FILE"
        
        echo "Worker $worker: $verification (checksum: ${checksum:0:16}...)"
        
        if [ "$verification" != "OK" ]; then
            all_valid=false
        fi
    else
        echo "Worker $worker: RESULT_MISSING"
        all_valid=false
    fi
done

echo "" >> "$RESULTS_FILE"
echo "  ]," >> "$RESULTS_FILE"
echo "  \"all_valid\": $all_valid," >> "$RESULTS_FILE"
echo "  \"completed_at\": \"$(date -Iseconds)\"" >> "$RESULTS_FILE"
echo "}" >> "$RESULTS_FILE"

echo "=============================================="
echo "Stress test complete. All valid: $all_valid"
echo "Results saved to: $RESULTS_FILE"

# Check database consistency
db_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM artifacts WHERE task_id = '$TASK_ID' AND name LIKE 'stress_test_worker_%';")
echo "Artifacts registered in DB: $db_count"

if [ "$all_valid" = true ] && [ "$db_count" -eq 5 ]; then
    echo "SUCCESS: All 5 artifacts written and verified correctly"
    exit 0
else
    echo "FAILURE: Some artifacts missing or corrupted"
    exit 1
fi
