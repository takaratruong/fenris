# Task Update: tsk_4cd00256bea7
## Review baseline eval criteria and risks
**Thread:** thr_1621f695c75d  
**Status:** In Progress → Completed  
**Timestamp:** 2026-04-10T05:47:00Z

---

## Baseline Eval Criteria Review

### 1. System/Infrastructure Baseline (from Fenris Stress Test 3)

| Criterion | Baseline Value | Source |
|-----------|---------------|--------|
| CPU idle | 2.6% utilization | metrics.json |
| Memory baseline | 9.2% of 1.5TB | metrics.json |
| Load average (idle) | 15.14 | metrics.json |
| I/O throughput | 30.7 GB/s | stress test |
| Concurrent process capacity | 100+ without failure | stress test |

**Pass criteria established:**
- No OOM kills during test
- CPU sustains target load without thermal throttling
- I/O completes without errors
- System remains responsive throughout

### 2. 3DGS Evaluation Baselines (from memory/research logs)

| Metric | Baseline | Context |
|--------|----------|---------|
| PSNR (30 frames) | ~26 | Standard training quality |
| PSNR (60 frames) | ~25.5 | Best balance for walking dataset |
| Novel view extrapolation | ~5-10cm | Beyond this, quality degrades |
| FreeSplatter throughput | 520k gaussians/0.63s | 2-view scene reconstruction |

### 3. Metrics Collection Infrastructure

Current state (from collector scan):
- **Active lanes:** 2 (bench, research)
- **Indexed artifacts:** 1 (research)
- **Metrics files:** 1 (bench)

---

## Identified Risks

### High Priority

1. **Disk capacity at 98%** - Stress test flagged this. Write-heavy evaluations may fail or produce corrupted results. **Mitigation:** Cleanup before large-scale eval runs.

2. **Sparse baseline coverage** - Only bench lane has metrics; other lanes (engineer, ops, lab) have no baseline metrics. Comparisons will be incomplete.

3. **Narrow viewing frustum problem** - 3DGS quality degrades rapidly beyond ~10cm extrapolation. Any eval assuming wider novel view generation will produce misleading results.

### Medium Priority

4. **Depth alignment issues** - MASt3R depth supervision showed PSNR regression (26→20.1) due to pose/depth misalignment. Depth-supervised evals need careful calibration.

5. **Frame density sweet spot** - 120 frames performed worse than 60 due to MASt3R optimization issues with near-identical views. Eval criteria should specify optimal frame sampling, not "more is better."

6. **FreeSplatter 2-view limitation** - Current model only supports 2 input views despite architecture supporting more. Multi-view eval criteria shouldn't assume this extends cleanly.

### Low Priority

7. **CPU underutilization in stress tests** - 16-worker test only used ~8% of 192 cores. Baseline doesn't reflect true system capacity under full load.

---

## Recommendations

1. **Establish per-lane baseline requirements** before cross-lane evaluation comparisons
2. **Add disk space monitoring** as a pre-flight check for all eval runs
3. **Document frame sampling guidelines** (60 frames optimal for 4s walking sequences)
4. **Separate extrapolation metrics** from reconstruction metrics in 3DGS evals
5. **Create a risk register** artifact for ongoing risk tracking

---

## Artifacts Referenced
- `bench/fenris-stress-test-3/artifacts/metrics.json`
- `bench/fenris-stress-test-3/artifacts/summary-report.md`
- `engineer/metrics/collected-20260410-054907.json`
- `research/artifacts/index.json`
- `memory/2026-04-01.md` (3DGS research log)

---

**Task Status:** COMPLETE  
**Next action:** Await thread owner review or follow-up task assignment
