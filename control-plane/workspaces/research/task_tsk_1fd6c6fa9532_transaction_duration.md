# Transaction Duration Impact on SQLite Locking

**Task:** tsk_1fd6c6fa9532  
**Thread:** thr_c13d17601e0c  
**Date:** 2026-04-10

---

## Assessment (1-Paragraph)

SQLite uses a three-stage lock progression—SHARED (readers), RESERVED→PENDING (writer preparing), and EXCLUSIVE (commit in progress)—and **holds EXCLUSIVE for the entire write transaction duration**, meaning any work done between BEGIN and COMMIT (serialization, computation, network calls) blocks all other writers. **Shorter transactions directly reduce lock contention** because the exclusive lock window shrinks: moving data preparation outside transactions (20-50% reduction), batching 10-50 ops per commit (10-25% reduction), and using `BEGIN IMMEDIATE` (fail-fast rather than mid-transaction SQLITE_BUSY) are the highest-ROI patterns. **Key tradeoffs**: more commits mean more fsyncs (mitigated by WAL mode's sequential writes), transaction splitting risks partial failures and lost atomicity, and the benefit diminishes if transactions are already short. **Recommendation**: start with `BEGIN IMMEDIATE` everywhere (30 min), then move serialization/computation outside transaction boundaries (2-4 hrs per module), then profile—these quick wins typically provide the best ROI before considering more invasive changes like a write queue.

---

## Lock Type Reference

| Lock | Acquired When | Blocks |
|------|---------------|--------|
| SHARED | First read | Writers from EXCLUSIVE |
| RESERVED | First write in txn | New writers (one RESERVED allowed) |
| PENDING | Writer starting commit | New SHARED acquisitions |
| EXCLUSIVE | During commit | Everything |

---

## Recommended Patterns (Priority Order)

1. **BEGIN IMMEDIATE** — acquire lock immediately; fail fast on contention (30 min)
2. **Prepare-execute separation** — serialize/compute outside transaction (2-4 hrs)
3. **Batch sizing** — 10-50 ops per commit balances fsync overhead vs lock duration (1-2 hrs)
4. **Read-write separation** — use separate read connections; avoid holding write locks for SELECTs

---

## Tradeoffs Summary

| Shorter Transactions | Longer Transactions |
|---------------------|---------------------|
| More fsyncs (higher I/O) | Fewer fsyncs |
| Lower contention | Higher contention |
| Harder atomicity reasoning | Clear atomic boundaries |
| Higher partial-failure risk | Lower partial-failure risk |

---

## Sources

- Prior research: `artifacts/art_rs_003_shorter_transaction_patterns.json`
- SQLite lock documentation: https://sqlite.org/lockingv3.html
- SQLite WAL mode: https://sqlite.org/wal.html
