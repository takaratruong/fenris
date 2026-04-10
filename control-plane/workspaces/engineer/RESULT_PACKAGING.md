# Result Packaging Flow

Standardized artifact packaging for control-plane cross-lane visibility.

## Quick Start

```python
from result_packaging import ResultPackager, Visibility

# Initialize for your lane
packager = ResultPackager(lane="bench", agent="bench")

# Package benchmark results
artifact = packager.package_benchmark_result(
    test_id="my-test-1",
    title="My Benchmark Results",
    metrics={"result": "PASS", "duration": 10.5},
    task_id="tsk_xxx",
    thread_id="thr_xxx",
    tags=["benchmark", "performance"],
)

# Package probe/investigation reports
artifact = packager.package_probe_report(
    probe_id="probe-123",
    title="System Health Probe",
    findings={"status": "healthy", "checks": ["cpu", "mem"]},
)

# Package evidence for claims
artifact = packager.package_evidence(
    claim_id="clm_abc123",
    evidence_type="experimental",
    evidence_data={"observed": True, "confidence": 0.95},
    stance="supports",  # supports | weak_support | contradicts | invalidates
)
```

## CLI Usage

```bash
# Package benchmark
python3 package_result.py benchmark \
  --lane bench --test-id stress-1 --title "Stress Test" \
  --metrics-file metrics.json --task-id tsk_xxx

# Package probe report
python3 package_result.py probe \
  --lane ops --probe-id health-check --title "Health Check" \
  --findings-file findings.json

# Package evidence
python3 package_result.py evidence \
  --lane research --claim-id clm_123 --stance supports \
  --evidence-file evidence.json

# List artifacts in a lane
python3 package_result.py list --lane bench

# Discover cross-lane artifacts
python3 package_result.py discover
python3 package_result.py discover --tags benchmark,stress-test
```

## Artifact Schema

```json
{
  "artifact_id": "art_{lane}_{seq}_{slug}",
  "artifact_type": "benchmark_result | research_summary | evidence_collection | probe_report",
  "visibility": "lane-local | cross-lane | public",
  "tags": ["tag1", "tag2"],
  "created_by": {"agent": "...", "task_id": "...", "thread_id": "..."},
  "cross_lane_hooks": {"research": "why relevant", "ops": "why relevant"}
}
```

## Supported Types

| Type | Use Case |
|------|----------|
| `benchmark_result` | Performance/stress test metrics |
| `research_summary` | Literature reviews, surveys |
| `evidence_collection` | Claim support/contradiction data |
| `probe_report` | Investigation/diagnostic findings |
| `experiment_log` | Lab experiment records |

## Cross-Lane Discovery

All artifacts with `visibility: "cross-lane"` are discoverable from any lane:

```python
from result_packaging import discover_cross_lane_artifacts

# Find all cross-lane artifacts
all_artifacts = discover_cross_lane_artifacts()

# Filter by lane or tags
bench_artifacts = discover_cross_lane_artifacts(target_lane="bench")
tagged = discover_cross_lane_artifacts(tags=["benchmark", "fenris"])
```

## Integration with Control Plane

When creating artifacts from tasks, always include `task_id` and `thread_id` for traceability:

```python
packager.package_benchmark_result(
    test_id="test-1",
    title="Test Results",
    metrics=data,
    task_id="tsk_dddabcfe4c1c",  # Current task
    thread_id="thr_1621f695c75d",  # Parent thread
)
```

This links the artifact to its originating task/thread for audit trails.
