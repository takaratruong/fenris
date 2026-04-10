# Baseline B - Isolated Branch Implementation

**Task:** tsk_e5bcd016cfac  
**Thread:** thr_982538c0d49b  
**Created:** 2026-04-10T06:44:00Z  
**Status:** Active

## Design Principles

Baseline B operates with **strict isolation**:
- Separate branch memory from Baselines A and C
- Independent state management
- No cross-baseline contamination of metrics or artifacts
- Complete namespace separation at all levels

## Branch Memory Architecture

```
baseline_b/
├── README.md                 # This file
├── branch_state.json         # Isolated state (not shared)
├── baseline_b_harness.py     # Specialized harness for Baseline B
├── memory/                   # Branch-specific memory store
│   └── state.json           # Persistent state across runs
└── artifacts/               # Baseline B artifacts only
```

## Usage

```python
from baseline_b.baseline_b_harness import BaselineBHarness

with BaselineBHarness() as harness:
    # Run experiments
    harness.log_metric("throughput", 1000)
    harness.checkpoint("phase_complete")
    
    # Access isolated memory
    harness.memory_set("last_score", 0.95)
    score = harness.memory_get("last_score")
```

## Isolation Guarantees

1. **Memory Isolation**: `baseline_b/memory/` directory is exclusive to this branch
2. **Artifact Namespace**: All artifacts prefixed with `baseline_b_`
3. **No Shared State**: Branch state stored in `branch_state.json`, not global
4. **Independent Metrics**: Metrics collected and stored separately
5. **Identifier Uniqueness**: All run IDs prefixed with `b_` to prevent collision

## Key Differences from Baseline A

| Aspect | Baseline A | Baseline B |
|--------|-----------|-----------|
| Memory Path | `baseline_a/memory/` | `baseline_b/memory/` |
| Run ID Prefix | `a_run_` | `b_run_` |
| Artifact Prefix | `baseline_a_` | `baseline_b_` |
| State File | `baseline_a/branch_state.json` | `baseline_b/branch_state.json` |

## Integration with Control Plane

- Results feed back to thread thr_982538c0d49b
- Can serve as evidence for claims
- Compatible with cross-lane discovery (but maintains isolation)
- Does NOT share state with Baseline A or C
