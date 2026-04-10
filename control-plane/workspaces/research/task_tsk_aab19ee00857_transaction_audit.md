# Transaction Duration Audit: Control-Plane API Endpoints

**Task:** tsk_aab19ee00857  
**Thread:** thr_0439e7b6102d  
**Date:** 2026-04-10  
**Status:** Complete

---

## Executive Summary

Audited control-plane API write endpoints to identify which hold SQLite transactions open longest. The ranking below prioritizes refactoring candidates by lock-duration impact. Primary driver of extended lock windows: multiple table writes + side-effect cascades (events, lease cleanup) bundled in single `BEGIN IMMEDIATE` transactions.

---

## Ranked Refactoring Candidates (by Lock Duration Impact)

### 1. POST /updates → `db.insert_update` (HIGHEST IMPACT)

**Why it's #1:** Largest transaction fanout. The `kind=done` path chains:
- Task state update
- Event emission
- Lease cleanup  
- Worker state update
- Experiment closeout updates

All under one `BEGIN IMMEDIATE` lock.

**Estimated lock window:** 15-50ms under load  
**Refactor approach:** Split into phase-1 (state write) + phase-2 (side-effects/events) or async outbox pattern.

---

### 2. POST /discord/messages → `db.bootstrap_thread_from_discord_message` (HIGH IMPACT)

**Why it's #2:** Large multi-insert/update transaction with:
- Loops over stale tasks (O(n) where n = stale count)
- Multiple `emit_event` calls within transaction
- Thread + task creation bundled

**Estimated lock window:** 20-100ms (varies with stale task count)  
**Refactor approach:** Move stale-task cleanup to periodic sweeper; batch event emissions outside tx.

---

### 3. POST /tasks/{task_id}/transition → `db.transition_task` (MEDIUM-HIGH IMPACT)

**Why it's #3:** 7 discrete writes plus `_apply_experiment_closeout` side effects:
- Task status update
- Event emission
- Lease state change
- Experiment state updates (if applicable)
- Metrics updates

**Estimated lock window:** 10-30ms  
**Refactor approach:** Separate core state transition from experiment/metrics side effects.

---

### 4. Cross-cutting: `_reconcile_expired_leases` (MULTIPLIER EFFECT)

**Why it matters:** Runs inside claim/heartbeat/transition transactions. Loops over ALL expired leases/events in one writer lock.

**Impact:** Multiplies lock duration of endpoints #1-3 above, especially under load when many leases expire.  
**Estimated lock extension:** 5-30ms per call (scales with expired lease count)  
**Refactor approach:** Move to periodic sweeper OR batch limit (reconcile max N per call).

---

### 5. POST /claims → `db.claim_task` (MEDIUM IMPACT)

Includes:
- Task state update
- Lease creation
- Event emission
- `_reconcile_expired_leases` call

**Estimated lock window:** 8-20ms  
**Refactor approach:** Already benefits from reconcile-sweeper extraction.

---

### 6. POST /heartbeat → `db.heartbeat` (LOWER IMPACT, HIGH FREQUENCY)

Simpler transaction but called frequently:
- Lease touch
- `_reconcile_expired_leases` call

**Estimated lock window:** 3-15ms  
**Refactor approach:** Reconcile extraction provides most benefit.

---

## Common Lock-Extender Patterns

| Pattern | Occurrence | Impact |
|---------|------------|--------|
| `_reconcile_expired_leases` in write tx | claim, heartbeat, transition | +5-30ms |
| `emit_event` inside tx | most write endpoints | +2-5ms each |
| Experiment closeout cascade | done/failed updates, transitions | +5-15ms |
| Stale task loop | discord bootstrap | +2-5ms per task |

---

## Recommended Refactor Priority

1. **Extract `_reconcile_expired_leases`** → periodic sweeper with batch limit (affects 4+ endpoints)
2. **Split `db.insert_update` done-path** → core write + async side-effects
3. **Optimize discord bootstrap** → separate stale cleanup from thread creation
4. **Event emission batching** → buffer + batch outside tx boundary

---

## Notes

- Source audit performed on `/home/takaret/projects/openclaw_control_plane`
- Lock duration estimates based on code analysis (no runtime profiling in this audit)
- Engineer task `tsk_99fd7b040ab8` has implemented some of these recommendations

---

## Related Research

- `task_tsk_1fd6c6fa9532_transaction_duration.md` - Lock progression mechanics
- `task_tsk_043d69feeec8_shorter_transactions.md` - Shorter transaction patterns
- `artifacts/art_rs_003_shorter_transaction_patterns.json` - Pattern catalog
