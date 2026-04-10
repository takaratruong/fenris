# Baseline Harness

A standardized experiment execution framework for control-plane lanes.

## Overview

The baseline harness provides:
- **Consistent setup/teardown** - Automatic environment capture and cleanup
- **Metrics collection** - System metrics + custom metrics with timestamps  
- **Artifact management** - Organized storage with cross-lane indexing
- **Reproducibility** - Config snapshots, environment capture, run IDs

## Quick Start

```python
from harness import BaselineHarness, ExperimentConfig

config = ExperimentConfig(
    experiment_id="my_experiment",
    name="attention-benchmark",
    lane="bench",
    tags=["benchmark", "attention"]
)

with BaselineHarness(config) as harness:
    # Your experiment code here
    harness.checkpoint("training_start")
    
    for epoch in range(10):
        loss = train_epoch()
        harness.log_metric("loss", loss, tags={"epoch": str(epoch)})
    
    harness.checkpoint("training_complete")
    harness.save_artifact("model_config", {"layers": 12, "heads": 8})
```

## Convenience Wrapper

For quick experiments:

```python
from harness import quick_harness

with quick_harness("quick-test", lane="engineer") as h:
    h.log_metric("score", 0.95)
    h.checkpoint("done")
```

## ExperimentConfig Options

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `experiment_id` | str | required | Unique identifier for this experiment |
| `name` | str | required | Human-readable name |
| `lane` | str | "engineer" | Which agent lane owns this (bench, research, etc.) |
| `metrics_interval_sec` | float | 1.0 | System metrics collection interval |
| `timeout_sec` | float | None | Optional timeout |
| `tags` | List[str] | [] | Tags for indexing and search |
| `parameters` | Dict | {} | Experiment parameters for reproducibility |
| `collect_system_metrics` | bool | True | Whether to collect system metrics |
| `artifact_retention_days` | int | 30 | How long to keep artifacts |

## Harness Methods

### `checkpoint(name, data=None)`
Record a named checkpoint with optional data payload.

### `log_metric(name, value, tags=None)`  
Log a custom metric with timestamp and optional tags.

### `save_artifact(name, content, artifact_type="json")`
Save an artifact. Types: "json", "text", "binary".

### `artifacts_dir` (property)
Path to the experiment's artifact directory.

## Generated Artifacts

Each experiment run creates:

```
<lane>/<experiment_id>/artifacts/
├── config_snapshot.json   # Config + system info + env vars
├── results.json           # Final results summary
├── system_metrics.json    # Time-series system metrics
└── <custom artifacts>     # Your saved artifacts
```

## Cross-Lane Discovery

Artifacts are indexed at `<lane>/artifacts/index.json` for cross-lane discovery:

```python
# Other lanes can discover experiments
import json
with open("/home/ubuntu/.openclaw/workspace/control-plane/workspaces/bench/artifacts/index.json") as f:
    index = json.load(f)
    recent = index["artifacts"][:10]
```

## Integration with Control Plane

The harness integrates with the control-plane research model:
- Artifacts can serve as evidence for claims
- Results support thread belief updates
- Tags enable filtering by topic/purpose

## System Metrics Collected

When `collect_system_metrics=True`:
- `load_1m`, `load_5m`, `load_15m` - CPU load averages
- `mem_used_percent` - Memory utilization
- Timestamps for time-series analysis
