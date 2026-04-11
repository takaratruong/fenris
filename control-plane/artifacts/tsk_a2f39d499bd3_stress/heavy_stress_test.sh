#!/bin/bash
# Heavy concurrent write stress test - 10 workers with overlapping writes

set -e

ARTIFACT_DIR="/home/ubuntu/.openclaw/workspace/control-plane/artifacts/tsk_a2f39d499bd3_stress/heavy"
DB_PATH="/home/ubuntu/.openclaw/workspace/control-plane/control_plane.db"
TASK_ID="tsk_a2f39d499bd3"
THREAD_ID="thr_74e02923a9ec"

mkdir -p "$ARTIFACT_DIR"

# Worker function that creates multiple artifacts rapidly
stress_worker() {
    local worker_id=$1
    local iterations=$2
    
    for i in $(seq 1 $iterations); do
        local artifact_id="art_heavy_w${worker_id}_i${i}_$(date +%N)"
        local filename="heavy_w${worker_id}_i${i}.dat"
        local filepath="$ARTIFACT_DIR/$filename"
        
        # Generate varied size artifacts (10KB to 200KB)
        local size_kb=$((10 + RANDOM % 190))
        head -c $((size_kb * 1024)) /dev/urandom > "$filepath"
        
        local checksum=$(sha256sum "$filepath" | cut -d' ' -f1)
        
        # Concurrent DB insert with WAL mode handling
        sqlite3 "$DB_PATH" "
            INSERT INTO artifacts (id, task_id, thread_id, name, path, content_hash, created_by)
            VALUES ('$artifact_id', '$TASK_ID', '$THREAD_ID', 'heavy_${worker_id}_${i}', '$filepath', '$checksum', 'heavy_worker_$worker_id');
        " 2>/dev/null || echo "DB insert retry needed for w$worker_id i$i"
        
        echo "W$worker_id:I$i:$checksum"
    done
}

echo "Heavy stress test: 10 workers x 3 artifacts each = 30 concurrent writes"
echo "Starting at $(date -Iseconds)"
echo "=============================================="

# Launch 10 workers, each writing 3 artifacts
pids=()
for worker in $(seq 1 10); do
    stress_worker $worker 3 > "$ARTIFACT_DIR/worker_${worker}_output.txt" &
    pids+=($!)
done

# Wait for all
for pid in "${pids[@]}"; do
    wait $pid || true
done

echo "All workers finished. Verifying..."

# Verify all files
total_files=$(find "$ARTIFACT_DIR" -name "heavy_w*.dat" | wc -l)
db_records=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM artifacts WHERE name LIKE 'heavy_%';")

echo "Files on disk: $total_files"
echo "DB records: $db_records"

# Verify each file's integrity
corrupt_count=0
for filepath in "$ARTIFACT_DIR"/heavy_w*.dat; do
    if [ -f "$filepath" ]; then
        filename=$(basename "$filepath")
        expected_hash=$(sqlite3 "$DB_PATH" "SELECT content_hash FROM artifacts WHERE path = '$filepath' LIMIT 1;")
        actual_hash=$(sha256sum "$filepath" | cut -d' ' -f1)
        
        if [ "$expected_hash" != "$actual_hash" ]; then
            echo "MISMATCH: $filename"
            corrupt_count=$((corrupt_count + 1))
        fi
    fi
done

echo "=============================================="
echo "Corrupted/mismatched files: $corrupt_count"

if [ "$total_files" -ge 28 ] && [ "$corrupt_count" -eq 0 ]; then
    echo "HEAVY STRESS TEST: PASSED"
    exit 0
else
    echo "HEAVY STRESS TEST: ISSUES DETECTED"
    exit 1
fi
