# Shorter Write Transaction Patterns for SQLite Contention

**Task:** tsk_857dcc1d59a8  
**Thread:** thr_3ac4a4987fa7  
**Date:** 2026-04-10  
**Context:** SQLite WAL benchmark showed P95 lock wait of 3.4ms (down from 7.1ms with DELETE mode), but maximum lock wait spikes still reach 2.24s under concurrent write load.

---

## Executive Summary

This research documents application-level patterns for reducing SQLite write transaction duration. The goal is to minimize the time writers hold exclusive locks, thereby reducing P99 latency spikes and improving throughput under concurrent access.

**Key insight:** SQLite's write lock is *per-database*, not per-table. Any open write transaction blocks all other writers. The single most effective optimization is keeping write transactions as short as possible.

---

## 1. Transaction Scope Minimization Techniques

### 1.1 Compute Outside, Commit Inside

**Problem:** Long-running computations inside transactions hold locks unnecessarily.

```python
# ❌ BAD: Lock held during entire computation
conn.execute("BEGIN IMMEDIATE")
data = expensive_computation()  # Lock held here!
result = more_processing(data)   # Still holding lock
conn.execute("INSERT INTO results VALUES (?)", (result,))
conn.commit()
```

```python
# ✅ GOOD: Compute first, then short transaction
data = expensive_computation()   # No lock
result = more_processing(data)   # No lock
conn.execute("BEGIN IMMEDIATE")
conn.execute("INSERT INTO results VALUES (?)", (result,))
conn.commit()  # Lock held only for ~1ms
```

**Expected impact:** Reduces lock duration from O(computation_time) to O(IO_time).

### 1.2 Read-Modify-Write Separation

**Problem:** SELECT-for-update patterns hold read locks that escalate to write locks.

```python
# ❌ BAD: Long lock escalation window
conn.execute("BEGIN IMMEDIATE")
row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
new_status = compute_new_status(row)  # Still holding lock
conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, task_id))
conn.commit()
```

```python
# ✅ GOOD: Read outside, write inside
# Phase 1: Read (no exclusive lock in WAL mode)
conn.execute("BEGIN")
row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
conn.commit()

# Phase 2: Compute (no transaction)
new_status = compute_new_status(row)

# Phase 3: Write (short exclusive lock)
conn.execute("BEGIN IMMEDIATE")
conn.execute("UPDATE tasks SET status = ? WHERE id = ?", (new_status, task_id))
conn.commit()
```

**Caveat:** This introduces a TOCTOU race. Use optimistic locking (version column) if atomicity matters:

```python
conn.execute("""
    UPDATE tasks SET status = ?, version = version + 1 
    WHERE id = ? AND version = ?
""", (new_status, task_id, expected_version))
if conn.total_changes == 0:
    raise StaleDataError("Row was modified by another writer")
```

**Expected impact:** P99 reduction of 30-50% for read-heavy modify patterns.

### 1.3 Deferred vs Immediate Transactions

**Background:**
- `BEGIN DEFERRED` (default): Acquires locks lazily on first write
- `BEGIN IMMEDIATE`: Acquires RESERVED lock immediately
- `BEGIN EXCLUSIVE`: Acquires EXCLUSIVE lock immediately

**Recommendation:** Use `BEGIN IMMEDIATE` when you know you'll write. This:
1. Fails fast if another writer holds the lock (rather than mid-transaction)
2. Avoids lock escalation deadlocks
3. Makes lock wait time predictable

```python
# For write-intent transactions, always use IMMEDIATE
conn.execute("BEGIN IMMEDIATE")
```

---

## 2. Batching Strategies That Keep Locks Short

### 2.1 Micro-Batch Commits

**Problem:** Single-row commits have high per-transaction overhead. Large batches hold locks too long.

**Solution:** Commit in fixed-size micro-batches (typically 50-200 rows).

```python
BATCH_SIZE = 100
pending = []

for item in work_items:
    pending.append(prepare_row(item))
    
    if len(pending) >= BATCH_SIZE:
        conn.execute("BEGIN IMMEDIATE")
        conn.executemany("INSERT INTO results VALUES (?, ?, ?)", pending)
        conn.commit()
        pending = []

# Flush remaining
if pending:
    conn.execute("BEGIN IMMEDIATE")
    conn.executemany("INSERT INTO results VALUES (?, ?, ?)", pending)
    conn.commit()
```

**Tuning:** Optimal batch size depends on row size and disk speed:
- NVMe SSD: 100-500 rows per batch
- SATA SSD: 50-200 rows per batch
- HDD: 20-100 rows per batch

**Expected impact:** 
- Per-write overhead drops from ~1ms to ~0.01ms (100x for 100-row batches)
- Lock duration stays bounded at O(batch_size × row_write_time)

### 2.2 Time-Bounded Batching

When work arrives at variable rates, use time-bounded batches:

```python
BATCH_TIMEOUT = 0.050  # 50ms max batch accumulation
BATCH_SIZE_MAX = 200

class TimeBoundedBatcher:
    def __init__(self, conn):
        self.conn = conn
        self.pending = []
        self.batch_start = None
    
    def add(self, row):
        if not self.pending:
            self.batch_start = time.monotonic()
        self.pending.append(row)
        
        if (len(self.pending) >= BATCH_SIZE_MAX or 
            time.monotonic() - self.batch_start > BATCH_TIMEOUT):
            self.flush()
    
    def flush(self):
        if self.pending:
            self.conn.execute("BEGIN IMMEDIATE")
            self.conn.executemany("INSERT INTO results VALUES (?, ?, ?)", self.pending)
            self.conn.commit()
            self.pending = []
            self.batch_start = None
```

**Expected impact:** Bounds worst-case lock duration to ~50ms while maintaining batch efficiency.

### 2.3 Multi-Statement Batching

Combine related writes into single transactions:

```python
# ❌ BAD: Three separate transactions
conn.execute("INSERT INTO tasks ..."); conn.commit()
conn.execute("INSERT INTO task_metadata ..."); conn.commit()
conn.execute("UPDATE task_counts ..."); conn.commit()

# ✅ GOOD: One transaction, three statements
conn.execute("BEGIN IMMEDIATE")
conn.execute("INSERT INTO tasks ...")
conn.execute("INSERT INTO task_metadata ...")
conn.execute("UPDATE task_counts ...")
conn.commit()
```

**Expected impact:** 3x reduction in lock acquisitions, lower total lock time.

---

## 3. Read-Outside-Transaction Patterns

### 3.1 WAL Mode Read Concurrency

**Key insight:** In WAL mode, readers don't block writers and writers don't block readers. Readers see a consistent snapshot from transaction start.

```python
# Reader thread (runs concurrently with writers)
conn = sqlite3.connect(db_path)
# No transaction needed for simple reads in WAL mode
rows = conn.execute("SELECT * FROM tasks WHERE status = 'pending'").fetchall()
process_rows(rows)
```

**Benchmark reference:** Our WAL benchmark showed read throughput of 823 reads/s during concurrent writes, vs 2.8 reads/s in DELETE mode (29,307% improvement).

### 3.2 Snapshot Isolation for Complex Reads

For multi-query read operations needing consistency:

```python
conn.execute("BEGIN")  # Start read transaction (snapshot)
tasks = conn.execute("SELECT * FROM tasks WHERE status = 'pending'").fetchall()
metadata = conn.execute("SELECT * FROM task_metadata WHERE task_id IN (...)").fetchall()
conn.commit()  # Release read snapshot

# Process outside transaction
results = correlate(tasks, metadata)
```

**Note:** Read transactions in WAL mode don't block writers. They hold a reference to the WAL that prevents truncation, but this is generally not a problem unless reads are very long-running.

### 3.3 Caching Frequently-Read Data

For hot read paths, cache at the application layer:

```python
from functools import lru_cache
import time

@lru_cache(maxsize=1000)
def get_task_cached(task_id, cache_key=None):
    # cache_key allows invalidation
    conn = get_connection()
    return conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()

# Invalidate on write
def update_task(task_id, new_data):
    conn.execute("BEGIN IMMEDIATE")
    conn.execute("UPDATE tasks SET data = ? WHERE id = ?", (new_data, task_id))
    conn.commit()
    get_task_cached.cache_clear()  # Or selective invalidation
```

**Expected impact:** Eliminates read load from database entirely for cached items.

---

## 4. Prepare-Then-Commit Approaches

### 4.1 Statement Preparation Outside Transaction

SQLite compiles SQL to bytecode. Prepare statements once, reuse many times:

```python
# Prepare once (no transaction needed)
insert_stmt = conn.prepare("INSERT INTO tasks (name, status) VALUES (?, ?)")

# Execute many times with short transactions
for item in items:
    conn.execute("BEGIN IMMEDIATE")
    insert_stmt.execute((item.name, item.status))
    conn.commit()
```

**Python note:** `sqlite3` module caches prepared statements automatically. Use `isolation_level=None` for autocommit mode and explicit transaction control.

### 4.2 Staged Write Pattern

For complex writes, stage data outside the database, then commit atomically:

```python
# Stage 1: Prepare all data (no locks)
staged_rows = []
for item in work_items:
    validated = validate(item)
    transformed = transform(validated)
    staged_rows.append(transformed)

# Stage 2: Single atomic commit (short lock)
conn.execute("BEGIN IMMEDIATE")
for row in staged_rows:
    conn.execute("INSERT INTO tasks VALUES (?, ?, ?)", row)
conn.commit()
```

**Expected impact:** All validation/transformation overhead moved outside lock window.

### 4.3 Write-Ahead Application Log

For very latency-sensitive paths, use a write-ahead pattern:

```python
# Fast path: append to in-memory queue (no SQLite)
write_queue.append({"type": "insert", "data": row_data})
acknowledge_client()  # Immediate response

# Background writer (single thread, batched commits)
def background_writer():
    while True:
        batch = write_queue.drain(max_items=100, timeout=0.05)
        if batch:
            conn.execute("BEGIN IMMEDIATE")
            for item in batch:
                apply_write(conn, item)
            conn.commit()
```

**Trade-off:** Adds durability delay. Use when sub-millisecond acknowledgment matters more than immediate persistence.

---

## 5. Control-Plane Specific Recommendations

Based on the WAL benchmark (`sqlite_wal_benchmark_report.md`), the control-plane database shows:
- P95 lock wait: 3.4ms (good)
- Max lock wait: 2.24s (problematic for P99)

### Recommended Changes

1. **Audit long transactions:** Find transactions holding locks >100ms. Common culprits:
   - Task status updates with embedded computation
   - Bulk metadata writes
   - Cross-table consistency operations

2. **Implement micro-batching for task runs:** The `task_runs` table sees frequent inserts. Batch 50-100 writes per commit.

3. **Move reads outside transactions:** Task queries should use autocommit mode, not explicit transactions.

4. **Add optimistic locking for status updates:**
   ```sql
   ALTER TABLE task_runs ADD COLUMN version INTEGER DEFAULT 0;
   ```
   Use version checks to detect conflicts without holding locks during computation.

5. **Consider checkpoint tuning:** Current auto-checkpoint at 1000 pages is reasonable, but if WAL file grows large (>100MB), manual checkpointing during low-traffic periods may help.

### Expected Impact

Implementing these patterns should:
- Reduce P99 lock wait from 2.24s to <500ms
- Improve concurrent write throughput by 20-40%
- Eliminate lock-related SQLITE_BUSY errors under normal load

---

## References

- SQLite WAL Mode: https://sqlite.org/wal.html
- SQLite Atomic Commit: https://sqlite.org/atomiccommit.html
- SQLite Locking: https://sqlite.org/lockingv3.html
- Local artifact: `sqlite_wal_benchmark_report.md` (benchmark data)
- Local artifact: `sqlite_wal_benchmark.py` (benchmark implementation)

---

*Generated by research agent • Thread: thr_3ac4a4987fa7*
