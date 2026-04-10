#!/bin/bash
# Metrics Collector for Control-Plane Lanes
# Task: tsk_fd473154869d | Thread: thr_1621f695c75d
# 
# Collects metrics from lane artifacts and consolidates into a unified view.
# Designed to be run periodically or on-demand.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONTROL_PLANE_ROOT="$(dirname "$(dirname "$(dirname "$SCRIPT_DIR")")")" 
METRICS_DIR="$SCRIPT_DIR"
TIMESTAMP=$(date -u +%Y-%m-%dT%H:%M:%SZ)
OUTPUT_FILE="$METRICS_DIR/collected-$(date -u +%Y%m%d-%H%M%S).json"

echo "=== Metrics Collector ===" >&2
echo "Timestamp: $TIMESTAMP" >&2
echo "Control Plane Root: $CONTROL_PLANE_ROOT" >&2

# Initialize metrics structure
cat > "$OUTPUT_FILE" << EOF
{
  "collection_id": "col_$(date +%s)",
  "timestamp": "$TIMESTAMP",
  "collector_version": "1.0.0",
  "lanes": {}
}
EOF

# Function to safely extract JSON value
json_val() {
    local file="$1"
    local key="$2"
    python3 -c "import json; d=json.load(open('$file')); print(json.dumps(d.get('$key', None)))" 2>/dev/null || echo "null"
}

# Collect from each lane
collect_lane_metrics() {
    local lane="$1"
    local lane_dir="$CONTROL_PLANE_ROOT/workspaces/$lane"
    
    if [ ! -d "$lane_dir" ]; then
        echo "  Lane $lane: not found" >&2
        return
    fi
    
    echo "  Scanning lane: $lane" >&2
    
    # Look for metrics.json files in artifacts
    local metrics_files=$(find "$lane_dir" -name "metrics.json" -type f 2>/dev/null || true)
    local artifact_count=0
    local latest_metric=""
    
    for mf in $metrics_files; do
        artifact_count=$((artifact_count + 1))
        latest_metric="$mf"
    done
    
    # Look for index.json artifacts
    local index_file="$lane_dir/artifacts/index.json"
    local indexed_artifacts=0
    if [ -f "$index_file" ]; then
        indexed_artifacts=$(python3 -c "import json; print(len(json.load(open('$index_file')).get('artifacts', [])))" 2>/dev/null || echo 0)
    fi
    
    echo "    Found $artifact_count metrics files, $indexed_artifacts indexed artifacts" >&2
    
    # Output lane summary
    echo "{\"metrics_files\": $artifact_count, \"indexed_artifacts\": $indexed_artifacts, \"latest_metric\": $([ -n "$latest_metric" ] && echo "\"$latest_metric\"" || echo "null")}"
}

# Build lanes object
echo "Collecting from lanes..." >&2
LANES_JSON="{"
first=true
for lane in bench research engineer ops lab chief_of_staff orchestrator; do
    result=$(collect_lane_metrics "$lane")
    if [ -n "$result" ]; then
        if [ "$first" = true ]; then
            first=false
        else
            LANES_JSON="$LANES_JSON,"
        fi
        LANES_JSON="$LANES_JSON\"$lane\": $result"
    fi
done
LANES_JSON="$LANES_JSON}"

# Update output file with lanes data
python3 << PYEOF
import json

with open("$OUTPUT_FILE", "r") as f:
    data = json.load(f)

data["lanes"] = json.loads('''$LANES_JSON''')

# Compute summary
total_metrics = sum(l.get("metrics_files", 0) for l in data["lanes"].values())
total_artifacts = sum(l.get("indexed_artifacts", 0) for l in data["lanes"].values())
active_lanes = len([l for l in data["lanes"].values() if l.get("metrics_files", 0) > 0 or l.get("indexed_artifacts", 0) > 0])

data["summary"] = {
    "total_metrics_files": total_metrics,
    "total_indexed_artifacts": total_artifacts,
    "active_lanes": active_lanes,
    "lanes_scanned": len(data["lanes"])
}

with open("$OUTPUT_FILE", "w") as f:
    json.dump(data, f, indent=2)

print(f"Metrics collected: {total_metrics} metrics files, {total_artifacts} indexed artifacts across {active_lanes} active lanes")
PYEOF

echo "" >&2
echo "Output: $OUTPUT_FILE" >&2
cat "$OUTPUT_FILE"
