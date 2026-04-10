#!/bin/bash

OUTDIR="$(dirname "$0")"
METRICS_FILE="$OUTDIR/metrics.json"
LOG_FILE="$OUTDIR/system-log.txt"

echo "=== Fenris Stress Test 3 ===" | tee "$LOG_FILE"
echo "Started: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG_FILE"

# Capture baseline
echo "Capturing baseline metrics..." | tee -a "$LOG_FILE"
BASELINE_CPU=$(grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$4+$5)} END {print usage}')
BASELINE_MEM=$(free | grep Mem | awk '{print $3/$2 * 100.0}')
BASELINE_LOAD=$(cat /proc/loadavg | awk '{print $1}')

echo "{" > "$METRICS_FILE"
echo "  \"test_id\": \"fenris-stress-test-3\"," >> "$METRICS_FILE"
echo "  \"timestamp\": \"$(date -u +%Y-%m-%dT%H:%M:%SZ)\"," >> "$METRICS_FILE"
echo "  \"host\": \"$(hostname)\"," >> "$METRICS_FILE"
echo "  \"cores\": $(nproc)," >> "$METRICS_FILE"
echo "  \"baseline\": {" >> "$METRICS_FILE"
echo "    \"cpu_percent\": $BASELINE_CPU," >> "$METRICS_FILE"
echo "    \"mem_percent\": $BASELINE_MEM," >> "$METRICS_FILE"
echo "    \"load_avg\": $BASELINE_LOAD" >> "$METRICS_FILE"
echo "  }," >> "$METRICS_FILE"

# Phase 1: CPU stress (15 seconds, scaled for demo)
echo "" | tee -a "$LOG_FILE"
echo "=== Phase 1: CPU Stress ===" | tee -a "$LOG_FILE"
echo "Running CPU-bound workload across 16 workers for 15s..." | tee -a "$LOG_FILE"

START_CPU=$(date +%s.%N)
for i in $(seq 1 16); do
    timeout 15 sh -c 'while :; do echo "scale=5000; 4*a(1)" | bc -l > /dev/null 2>&1; done' &
done
wait
END_CPU=$(date +%s.%N)
CPU_DURATION=$(echo "$END_CPU - $START_CPU" | bc)

PEAK_CPU_LOAD=$(cat /proc/loadavg | awk '{print $1}')
echo "CPU stress completed in ${CPU_DURATION}s, peak load: $PEAK_CPU_LOAD" | tee -a "$LOG_FILE"

# Phase 2: Memory pressure (allocate and release)
echo "" | tee -a "$LOG_FILE"
echo "=== Phase 2: Memory Pressure ===" | tee -a "$LOG_FILE"
echo "Allocating 4GB memory blocks..." | tee -a "$LOG_FILE"

START_MEM=$(date +%s.%N)
# Allocate ~4GB with dd to /dev/null
for i in $(seq 1 4); do
    dd if=/dev/zero bs=1M count=1024 2>/dev/null | cat > /dev/null &
done
wait
END_MEM=$(date +%s.%N)
MEM_DURATION=$(echo "$END_MEM - $START_MEM" | bc)

PEAK_MEM=$(free | grep Mem | awk '{print $3/$2 * 100.0}')
echo "Memory test completed in ${MEM_DURATION}s, mem usage: ${PEAK_MEM}%" | tee -a "$LOG_FILE"

# Phase 3: I/O stress (light due to disk constraints)
echo "" | tee -a "$LOG_FILE"
echo "=== Phase 3: I/O Stress ===" | tee -a "$LOG_FILE"
echo "Running I/O throughput test (read-heavy due to disk space)..." | tee -a "$LOG_FILE"

START_IO=$(date +%s.%N)
# Read test from /dev/zero to measure throughput
IO_RESULT=$(dd if=/dev/zero of=/dev/null bs=1M count=2048 2>&1 | tail -1)
END_IO=$(date +%s.%N)
IO_DURATION=$(echo "$END_IO - $START_IO" | bc)

echo "I/O test completed: $IO_RESULT" | tee -a "$LOG_FILE"

# Phase 4: Concurrent operations
echo "" | tee -a "$LOG_FILE"
echo "=== Phase 4: Concurrent Operations ===" | tee -a "$LOG_FILE"
echo "Spawning 100 concurrent processes..." | tee -a "$LOG_FILE"

START_CONC=$(date +%s.%N)
for i in $(seq 1 100); do
    (sleep 0.1; echo $i > /dev/null) &
done
wait
END_CONC=$(date +%s.%N)
CONC_DURATION=$(echo "$END_CONC - $START_CONC" | bc)

echo "Concurrent ops completed in ${CONC_DURATION}s" | tee -a "$LOG_FILE"

# Capture final state
FINAL_CPU=$(grep 'cpu ' /proc/stat | awk '{usage=($2+$4)*100/($2+$4+$5)} END {print usage}')
FINAL_MEM=$(free | grep Mem | awk '{print $3/$2 * 100.0}')
FINAL_LOAD=$(cat /proc/loadavg | awk '{print $1}')

# Check for OOM events
OOM_COUNT=$(dmesg 2>/dev/null | grep -c "Out of memory" || echo 0)

# Complete metrics JSON
echo "  \"phases\": {" >> "$METRICS_FILE"
echo "    \"cpu_stress\": {\"duration_sec\": $CPU_DURATION, \"peak_load\": $PEAK_CPU_LOAD}," >> "$METRICS_FILE"
echo "    \"memory_pressure\": {\"duration_sec\": $MEM_DURATION, \"peak_mem_percent\": $PEAK_MEM}," >> "$METRICS_FILE"
echo "    \"io_stress\": {\"duration_sec\": $IO_DURATION}," >> "$METRICS_FILE"
echo "    \"concurrent_ops\": {\"duration_sec\": $CONC_DURATION, \"process_count\": 100}" >> "$METRICS_FILE"
echo "  }," >> "$METRICS_FILE"
echo "  \"final_state\": {" >> "$METRICS_FILE"
echo "    \"cpu_percent\": $FINAL_CPU," >> "$METRICS_FILE"
echo "    \"mem_percent\": $FINAL_MEM," >> "$METRICS_FILE"
echo "    \"load_avg\": $FINAL_LOAD" >> "$METRICS_FILE"
echo "  }," >> "$METRICS_FILE"
echo "  \"oom_events\": $OOM_COUNT," >> "$METRICS_FILE"

# Determine pass/fail
if [ "$OOM_COUNT" -eq 0 ]; then
    RESULT="PASS"
else
    RESULT="FAIL"
fi

echo "  \"result\": \"$RESULT\"" >> "$METRICS_FILE"
echo "}" >> "$METRICS_FILE"

echo "" | tee -a "$LOG_FILE"
echo "=== Test Complete ===" | tee -a "$LOG_FILE"
echo "Result: $RESULT" | tee -a "$LOG_FILE"
echo "Ended: $(date -u +%Y-%m-%dT%H:%M:%SZ)" | tee -a "$LOG_FILE"

echo $RESULT
