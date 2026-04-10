# Same-Lane Fanout Protocol

**Task:** tsk_c1f26922aab3  
**Thread:** thr_9d3f8b1b2253  
**Author:** engineer  
**Created:** 2026-04-10T09:33:00Z  
**Status:** Draft

## Overview

This protocol defines how 2-3 specialist agents can operate concurrently within a single lane while maintaining context coherence and clean result merging.

---

## 1. Branch Context Sharing

### 1.1 Shared Context Layer

Each lane maintains a **lane context envelope** that all specialists read from (but don't mutate directly):

```json
{
  "lane_id": "lane_xxx",
  "thread_id": "thr_xxx",
  "experiment_id": "exp_xxx",
  "shared_context": {
    "hypothesis": "...",
    "constraints": ["..."],
    "success_criteria": ["..."],
    "artifacts_available": ["art_xxx", "art_yyy"]
  },
  "fanout_epoch": 1,
  "created_at": "2026-04-10T09:30:00Z"
}
```

### 1.2 Specialist Branches

When fanout occurs, each specialist gets an **isolated branch view**:

```
lane_xxx/
├── context.json              # Shared (read-only for specialists)
├── fanout_epoch_1/
│   ├── specialist_a/
│   │   ├── branch_state.json # Private mutable state
│   │   ├── progress.json     # Heartbeat/progress tracking
│   │   └── artifacts/        # Branch-local artifacts
│   ├── specialist_b/
│   │   └── ...
│   └── specialist_c/
│       └── ...
└── merged/                   # Results after merge
```

### 1.3 Context Propagation Rules

| Context Type | Visibility | Mutability |
|--------------|------------|------------|
| Lane hypothesis | All specialists | Read-only |
| Experiment constraints | All specialists | Read-only |
| Cross-lane artifacts | All specialists | Read-only |
| Branch state | Own branch only | Read-write |
| Branch artifacts | Own branch (cross-visible after merge) | Write-only |

---

## 2. Result Merge Protocol

### 2.1 Merge Triggers

Results merge back when **any** of these conditions are met:

1. **All specialists complete** (ideal case)
2. **Timeout reached** (configurable, default 10 minutes)
3. **Early termination signal** (one specialist finds definitive answer)
4. **Capacity pressure** (lane needs to accept new work)

### 2.2 Merge Strategy

```python
class FanoutMerger:
    """Lightweight merge for same-lane specialist results."""
    
    def merge(self, branches: list[BranchResult]) -> MergedResult:
        # 1. Collect all branch artifacts
        artifacts = self._collect_artifacts(branches)
        
        # 2. Deduplicate evidence (same claim_id + stance = merge)
        evidence = self._dedupe_evidence(branches)
        
        # 3. Conflict resolution: latest-wins for metrics, union for findings
        metrics = self._merge_metrics(branches, strategy="latest_wins")
        findings = self._merge_findings(branches, strategy="union")
        
        # 4. Compute aggregate confidence
        confidence = self._aggregate_confidence(branches)
        
        return MergedResult(
            artifacts=artifacts,
            evidence=evidence,
            metrics=metrics,
            findings=findings,
            confidence=confidence,
            branches_merged=len(branches),
            merge_strategy="v1_lightweight"
        )
```

### 2.3 Conflict Resolution

| Data Type | Strategy | Rationale |
|-----------|----------|-----------|
| Metrics (numeric) | Latest timestamp wins | Assumes most recent is most accurate |
| Findings (list) | Union with dedup | Maximize coverage |
| Evidence stance | Weighted by confidence | Higher confidence prevails |
| Artifacts | All preserved | No data loss |

### 2.4 Merge Commit

After merge, a single **merge commit** is written to the lane:

```json
{
  "merge_id": "mrg_xxx",
  "fanout_epoch": 1,
  "branches_merged": ["specialist_a", "specialist_b"],
  "branches_incomplete": ["specialist_c"],
  "merge_reason": "all_complete | timeout | early_termination | capacity",
  "merged_at": "2026-04-10T09:45:00Z",
  "result_artifact_id": "art_lane_xxx_merged_1"
}
```

---

## 3. Lane Capacity Detection

### 3.1 Capacity Model

Each lane has a **soft capacity** of active specialists:

```python
LANE_CAPACITY = {
    "max_specialists": 3,          # Hard limit
    "target_specialists": 2,       # Preferred operating point
    "min_free_slots": 1,           # Reserve for urgent work
    "specialist_timeout_ms": 600000  # 10 minutes
}
```

### 3.2 Capacity Signals

The lane emits capacity status in its heartbeat:

```json
{
  "lane_id": "lane_xxx",
  "capacity": {
    "active_specialists": 2,
    "max_specialists": 3,
    "free_slots": 1,
    "status": "available | at_capacity | overloaded",
    "oldest_active_ms": 45000,
    "estimated_free_at": "2026-04-10T09:50:00Z"
  }
}
```

### 3.3 Capacity Status Logic

```python
def compute_capacity_status(lane) -> str:
    active = len(lane.active_specialists)
    max_cap = lane.config.max_specialists
    min_free = lane.config.min_free_slots
    
    if active == 0:
        return "idle"
    elif active + min_free <= max_cap:
        return "available"
    elif active < max_cap:
        return "at_capacity"  # Can accept urgent only
    else:
        return "overloaded"   # Must wait for completion
```

### 3.4 Backpressure Behavior

| Status | New Task Behavior |
|--------|-------------------|
| `idle` | Immediate dispatch |
| `available` | Immediate dispatch |
| `at_capacity` | Queue with priority, dispatch when slot frees |
| `overloaded` | Queue only, force-merge oldest if critical |

---

## 4. Implementation Checklist

### Phase 1: Core (Lightweight)
- [ ] Lane context envelope schema
- [ ] Specialist branch directory structure
- [ ] Simple merge function (union + latest-wins)
- [ ] Capacity counter in lane heartbeat

### Phase 2: Robustness
- [ ] Timeout-based forced merge
- [ ] Early termination signal handling
- [ ] Conflict logging for audit

### Phase 3: Optimization
- [ ] Predictive capacity (estimate specialist duration)
- [ ] Smart routing (send work to least-loaded lane)
- [ ] Cross-lane fanout (for overflow)

---

## 5. Example Flow

```
1. Thread thr_xxx spawns task requiring parallel investigation
2. Lane checks capacity: status="available", free_slots=2
3. Fanout creates epoch_1 with specialist_a, specialist_b
4. Each specialist:
   - Reads shared context (hypothesis, constraints)
   - Writes to own branch (state, artifacts)
   - Sends heartbeats with progress
5. specialist_a completes first, writes result
6. specialist_b completes, writes result  
7. Merger runs:
   - Collects artifacts from both branches
   - Dedupes evidence, merges findings
   - Writes merge commit
8. Lane updates capacity: free_slots=2 again
9. Merged result surfaces to thread as single artifact
```

---

## 6. Open Questions (for approval if needed)

1. **Timeout value**: 10 minutes default seems reasonable for most tasks. Should this be task-type specific?
2. **Force-merge policy**: When overloaded, should we force-merge the oldest fanout even if incomplete?
3. **Cross-lane overflow**: Should overloaded lanes route to sibling lanes, or just queue?

---

## Summary

This protocol enables lightweight same-lane fanout by:
- **Context sharing**: Read-only shared envelope + write-isolated branches
- **Result merging**: Union findings, latest-wins metrics, preserve all artifacts
- **Capacity detection**: Simple slot counting with soft/hard limits and backpressure

The design prioritizes simplicity: no distributed consensus, no complex locking, just directory isolation and a straightforward merge at completion.
