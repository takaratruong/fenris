#!/bin/bash
# Final validation: concurrent file writes with serialized DB access via flock

set -e

ARTIFACT_DIR="/home/ubuntu/.openclaw/workspace/control-plane/artifacts/tsk_a2f39d499bd3_stress/final"
DB_PATH="/home/ubuntu/.openclaw/workspace/control-plane/control_plane.db"
LOCK_FILE="/tmp/artifact_db.lock"
TASK_ID="tsk_a2f39d499bd3"
THREAD_ID="thr_74e02923a9ec"

mkdir -p "$ARTIFACT_DIR"

# Worker with proper locking
worker_with_lock() {
    local worker_id=$1
    local artifact_id="art_final_w${worker_id}_$(date +%N)"
    local filename="final_w${worker_id}.dat"
    local filepath="$ARTIFACT_DIR/$filename"
    
    # Write file (can be concurrent)
    local size_kb=$((50 + worker_id * 20))
    head -c $((size_kb * 1024)) /dev/urandom > "$filepath"
    local checksum=$(sha256sum "$filepath" | cut -d' ' -f1)
    
    # Serialized DB insert via flock
    (
        flock -x 200
        sqlite3 "$DB_PATH" "
            INSERT INTO artifacts (id, task_id, thread_id, name, path, content_hash, created_by)
            VALUES ('$artifact_id', '$TASK_ID', '$THREAD_ID', 'final_$worker_id', '$filepath', '$checksum', 'final_worker_$worker_id');
        "
    ) 200>"$LOCK_FILE"
    
    echo "$worker_id:$checksum"
}

echo "Final stress test: 5 concurrent file writes with serialized DB"
echo "=============================================="

# Launch 5 workers
pids=()
for worker in 1 2 3 4 5; do
    worker_with_lock $worker &
    pids+=($!)
done

# Wait for all
for pid in "${pids[@]}"; do
    wait $pid
done

# Verify
files_count=$(find "$ARTIFACT_DIR" -name "final_w*.dat" | wc -l)
db_count=$(sqlite3 "$DB_PATH" "SELECT COUNT(*) FROM artifacts WHERE name LIKE 'final_%';")

echo "Files written: $files_count"
echo "DB records: $db_count"

# Verify integrity
failures=0
for filepath in "$ARTIFACT_DIR"/final_w*.dat; do
    expected=$(sqlite3 "$DB_PATH" "SELECT content_hash FROM artifacts WHERE path = '$filepath';")
    actual=$(sha256sum "$filepath" | cut -d' ' -f1)
    if [ "$expected" = "$actual" ]; then
        echo "✓ $(basename $filepath)"
    else
        echo "✗ $(basename $filepath) - hash mismatch"
        failures=$((failures + 1))
    fi
done

echo "=============================================="
if [ "$files_count" -eq 5 ] && [ "$db_count" -eq 5 ] && [ "$failures" -eq 0 ]; then
    echo "FINAL STRESS TEST: PASSED"
    echo "All 5 artifacts written concurrently and verified"
else
    echo "FINAL STRESS TEST: FAILED"
fi
