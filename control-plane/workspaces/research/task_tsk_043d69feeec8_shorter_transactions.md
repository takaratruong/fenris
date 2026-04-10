# Shorter Transaction Strategies for SQLite Lock Contention

**Task:** tsk_043d69feeec8  
**Thread:** thr_a1cdce38a360  
**Status:** Complete  
**Date:** 2026-04-10

---

## Executive Summary

**Should you try shorter transactions first?** Yes, for quick wins—but they won't fully solve SQLITE_BUSY. Shorter transactions reduce contention 15-40% with low-medium effort, but only a write queue eliminates it entirely.

---

## 1. How Transaction Length Affects Lock Contention

SQLite uses database-level locking for writes. During a write transaction:

| Lock State | What Happens |
|------------|--------------|
| RESERVED | First write statement acquires; readers continue |
| PENDING | Commit starts; blocks new readers |  
| EXCLUSIVE | Commit in progress; blocks everything |

**Key insight:** The exclusive lock is held for the *entire transaction duration*. Any work done between BEGIN and COMMIT—computation, serialization, network calls—blocks all other writers.

```
Writer A: [BEGIN]--[work]--[work]--[work]--[COMMIT]
                   ↑ All other writers blocked here
Writer B:                            [SQLITE_BUSY retry loop...]
```

**Contention scales with:** (concurrent writers) × (avg transaction duration)

---

## 2. Patterns for Shortening Transactions

### Pattern A: Prepare-Execute Separation (Recommended First)
Move all data preparation outside transaction boundaries.

```python
# ❌ Lock held during preparation
with transaction:
    data = json.dumps(complex_object)  # Lock held
    timestamp = datetime.utcnow()       # Lock held  
    db.execute("INSERT ...", data)

# ✅ Minimal lock window
data = json.dumps(complex_object)       # No lock
timestamp = datetime.utcnow()           # No lock
with transaction:
    db.execute("INSERT ...", data)      # Lock only here
```

- **Contention reduction:** 20-50%
- **Effort:** 2-4 hours per module
- **Risk:** Minimal

### Pattern B: Batch Size Optimization
Balance fsync overhead vs lock duration.

| Batch Size | fsyncs | Lock Duration | Best For |
|------------|--------|---------------|----------|
| 1 op/txn | Many | Minimal | Low-volume, latency-sensitive |
| 10-50 ops/txn | Balanced | Short | Control-plane typical |
| 100+ ops/txn | Few | Extended | Bulk imports only |

- **Contention reduction:** 10-25%
- **Effort:** 1-2 hours
- **Risk:** Requires tuning

### Pattern C: Read-Write Separation
Keep reads outside write transactions; use separate connections for read-only queries.

```python
# Read phase (no write lock)
current = read_conn.execute("SELECT ...").fetchone()

# Write phase (minimal lock)  
with write_conn:
    write_conn.execute("UPDATE ... WHERE id = ?", ...)
```

- **Contention reduction:** 10-30%
- **Effort:** 3-6 hours (connection pool changes)
- **Risk:** Stale reads possible

### Pattern D: BEGIN IMMEDIATE (Quick Win)
For known-write transactions, acquire lock immediately rather than on first write.

```python
conn.execute("BEGIN IMMEDIATE")  # Acquire lock now
# ... do writes ...
conn.commit()
```

- **Benefit:** Fail-fast on contention (no surprise SQLITE_BUSY mid-transaction)
- **Effort:** 30 minutes
- **Risk:** None

---

## 3. Key Tradeoffs

| Concern | Shorter Txns | Longer Txns |
|---------|--------------|-------------|
| **fsync overhead** | More syncs = more I/O | Fewer syncs |
| **Code complexity** | Higher (scattered changes) | Lower |
| **Atomicity boundaries** | Harder to reason about | Clear boundaries |
| **Partial failure risk** | Higher (split operations) | Lower |
| **Lock contention** | Lower | Higher |

### fsync Overhead Detail
Each COMMIT triggers fsync (unless `PRAGMA synchronous=OFF`). With WAL mode:
- fsync is faster (sequential WAL writes)
- Batching 10-50 ops amortizes overhead well
- Below 10 ops: diminishing returns vs overhead

### Atomicity Boundaries Risk
Splitting `task_update + thread_belief_update + claim_create` across transactions:
- ❌ Crash between txns → inconsistent state
- ❌ Harder to reason about invariants
- ✅ Mitigation: idempotent operations, explicit `in_progress` states

---

## 4. Implementation Complexity

| Pattern | Lines Changed | Test Coverage | Rollback Difficulty |
|---------|---------------|---------------|---------------------|
| Prepare-execute | ~50-100/module | Unit tests | Easy |
| Batch sizing | ~20-50 | Integration tests | Easy |
| Read-write separation | ~100-200 | Integration tests | Medium |
| Transaction splitting | ~200+ | Full system tests | Hard |

**Recommended implementation order:**
1. BEGIN IMMEDIATE everywhere (30 min)
2. Prepare-execute separation (2-4 hrs)
3. Batch size tuning with profiling (1-2 hrs)
4. Read-write separation only if still contending

---

## 5. Decision Guidance: Try This First?

### ✅ Yes, try shorter transactions first IF:
- Current transactions do serialization/computation inside
- Contention is moderate (occasional SQLITE_BUSY)
- You want quick wins before bigger changes

### ❌ No, go straight to write queue IF:
- Contention is severe (frequent SQLITE_BUSY under load)
- Multiple writers are fundamental to architecture
- You need guaranteed SQLITE_BUSY elimination

### 🔄 Best approach: Hybrid
1. **Quick wins now:** BEGIN IMMEDIATE + prepare-execute separation
2. **Measure:** Profile actual transaction durations
3. **Then decide:** If still contending, add write queue for hot paths

---

## Comparison Summary

| Factor | Shorter Txns | Write Queue |
|--------|--------------|-------------|
| SQLITE_BUSY elimination | No (reduces) | Yes |
| Latency overhead | None | +10-15ms |
| Implementation scope | Distributed | Centralized |
| Quick wins available | Yes | No |
| Full solution | No | Yes |

**Bottom line:** Shorter transactions are a good first step with immediate benefits. They won't fully solve contention but buy time and reduce pressure while evaluating write queue.

---

## Sources

- SQLite documentation: locking, WAL, BEGIN IMMEDIATE
- Prior research: `artifacts/art_rs_003_shorter_transaction_patterns.json`
- Benchmark data: `bench/artifacts/sqlite_wal_benchmark_report.md`
