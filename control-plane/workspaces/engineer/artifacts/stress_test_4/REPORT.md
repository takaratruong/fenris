# Mixed Read/Write Stress Test Report

## Test Configuration
- **Readers**: 50
- **Writers**: 50
- **Duration**: 60.5 seconds
- **Database**: SQLite WAL mode

## Throughput Results

| Metric | Value |
|--------|-------|
| Total Operations | 46,736 |
| Total Throughput | 772.3 ops/sec |
| Read Operations | 35,699 |
| Read Throughput | 589.9 ops/sec |
| Write Operations | 11,037 |
| Write Throughput | 182.4 ops/sec |

## Read Latencies (under write contention)

| Percentile | Latency |
|------------|---------|
| Mean | 84.27 ms |
| p50 | 4.91 ms |
| p95 | 265.90 ms |
| p99 | 282.09 ms |
| Max | 309.19 ms |

## Write Latencies (under read contention)

| Percentile | Latency |
|------------|---------|
| Mean | 272.53 ms |
| p50 | 10.71 ms |
| p95 | 1635.58 ms |
| p99 | 3636.38 ms |
| Max | 9348.75 ms |

## Baseline Comparison

| Metric | Mixed Test | Baseline | Ratio |
|--------|-----------|----------|-------|
| Write Throughput | 182.4 ops/sec | 834 ops/sec | 21.9% |
| Write p99 | 3.636s | 2.7s | 134.7% |

## Errors
- Read Errors: 0
- Write Errors: 0

## Analysis

⚠️ **Write throughput degraded significantly** under read contention.

✅ **Write latency p99 acceptable**.

✅ **No errors**.

SQLite WAL mode allows concurrent readers during writes, which explains the read performance characteristics under write contention.
