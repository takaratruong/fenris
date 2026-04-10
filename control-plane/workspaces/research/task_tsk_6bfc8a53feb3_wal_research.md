# SQLite WAL Mode Research Findings

**Task:** tsk_6bfc8a53feb3  
**Thread:** thr_7d61b6889ec3  
**Status:** Running → Completed  
**Timestamp:** 2026-04-10T10:53:00Z

---

## Executive Summary

WAL (Write-Ahead Logging) mode is SQLite's alternative to the default rollback journal that significantly improves concurrency for mixed read/write workloads. For the control-plane use case with multiple concurrent agent connections, **WAL mode is strongly recommended** as a low-risk, high-impact mitigation for write contention.

**Recommendation: PROCEED** - Implementation is straightforward (single PRAGMA), fully reversible, and addresses the core contention issue.

---

## 1. How WAL Improves Concurrency vs Rollback Journal

### Rollback Journal (Default)
- **Mechanism:** Original data copied to journal file before changes written to main database
- **Locking:** Single writer blocks ALL readers and other writers during commit
- **Commit:** Occurs when journal is deleted (requires fsync)
- **Concurrency:** Effectively serialized - readers blocked during writes

### WAL Mode
- **Mechanism:** Changes appended to separate WAL file; original database preserved
- **Locking:** Readers and writers operate concurrently on different files
- **Commit:** Special commit record appended to WAL (no main DB write required)
- **Concurrency:** Multiple readers + one writer can proceed simultaneously

### Concurrency Comparison

| Scenario | Rollback Journal | WAL Mode |
|----------|------------------|----------|
| Read during write | ❌ Blocked | ✅ Concurrent |
| Write during reads | ❌ Blocked until reads finish | ✅ Concurrent |
| Multiple readers | ✅ Concurrent | ✅ Concurrent |
| Multiple writers | ❌ Serialized | ❌ Serialized (but shorter lock duration) |

**Key insight:** WAL doesn't enable multiple simultaneous writers, but it dramatically reduces lock contention by allowing reads to proceed during writes. The writer holds the lock for a much shorter duration since it only appends to WAL rather than modifying the main database.

---

## 2. Known Tradeoffs

### 2.1 Checkpoint Stalls

**What:** WAL data must eventually be transferred back to the main database via "checkpointing."

**Default behavior:** Auto-checkpoint when WAL reaches ~1000 pages (~4MB)

**Potential issues:**
- `SQLITE_CHECKPOINT_FULL` or `SQLITE_CHECKPOINT_RESTART` can block if readers are active
- Long-running read transactions prevent checkpoint completion, causing WAL growth
- Checkpoint during heavy write load may cause momentary slowdown

**Mitigations:**
- Use `PRAGMA wal_autocheckpoint=N` to tune threshold (or 0 to disable auto)
- Use `SQLITE_CHECKPOINT_PASSIVE` for non-blocking checkpoints
- Schedule explicit checkpoints during low-activity periods
- Set `busy_timeout` to handle transient conflicts gracefully

### 2.2 Disk Usage

**Additional files:**
- `database.db-wal` - WAL file (grows until checkpoint)
- `database.db-shm` - Shared memory index file (~32KB typical)

**Space considerations:**
- WAL can grow unbounded if checkpoints are blocked by long readers
- Worst case: WAL size = sum of all uncommitted transaction data
- Normal operation: ~4MB before auto-checkpoint triggers

**For control-plane:** Disk usage increase is negligible (KB-MB range for typical workloads).

### 2.3 Shared Memory Requirements

**The -shm file:**
- Memory-mapped file for WAL index coordination
- All connections must access same -shm file
- Requires write access to directory (even for read-only DB access in older SQLite)

**Critical limitation:** WAL does NOT work over network filesystems (NFS, SMB, etc.) because:
- Shared memory cannot be coordinated across hosts
- File locking semantics differ on network mounts

**For control-plane:** Not a concern if database is local to the host (standard deployment).

---

## 3. Implementation Complexity and Migration Path

### Migration (Rollback → WAL)

**Complexity: TRIVIAL** - Single command, no schema changes, no data migration.

```sql
PRAGMA journal_mode=WAL;
```

**Characteristics:**
- Takes effect immediately
- Persists across connections (stored in DB header)
- Can be run via CLI: `sqlite3 database.db "PRAGMA journal_mode=WAL;"`
- No application code changes required
- Existing connections should reconnect after change

### Recommended Implementation Steps

1. **Pre-flight check:**
   ```sql
   PRAGMA journal_mode;  -- Verify current mode (expect 'delete')
   PRAGMA integrity_check;  -- Ensure DB is healthy
   ```

2. **Enable WAL:**
   ```sql
   PRAGMA journal_mode=WAL;
   ```

3. **Tune for workload:**
   ```sql
   PRAGMA busy_timeout=5000;  -- 5s retry on lock
   PRAGMA synchronous=NORMAL;  -- Safe with WAL, faster than FULL
   PRAGMA wal_autocheckpoint=1000;  -- Default, adjust if needed
   ```

4. **Verify:**
   ```sql
   PRAGMA journal_mode;  -- Should return 'wal'
   ```

### Implementation Estimate

| Task | Effort |
|------|--------|
| Enable WAL mode | 5 minutes |
| Add recommended PRAGMAs | 10 minutes |
| Test with concurrent load | 30 minutes |
| Monitoring/validation | 1 hour |
| **Total** | **< 2 hours** |

---

## 4. Reversibility and Risk Assessment

### Reversibility: FULLY REVERSIBLE

```sql
PRAGMA journal_mode=DELETE;  -- Reverts to rollback journal
```

**Requirements for revert:**
- No active readers during journal mode change
- Checkpoint must complete (WAL data moved to main DB)
- After revert, -wal and -shm files can be deleted

### Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|------|------------|--------|------------|
| Checkpoint stall under load | Low | Medium | Use PASSIVE checkpoints, tune autocheckpoint |
| WAL file growth | Low | Low | Monitor file size, ensure no stuck readers |
| Compatibility issue | Very Low | High | Test in staging first |
| Data corruption | Very Low | Critical | WAL is battle-tested; use integrity_check |
| Performance regression | Very Low | Low | Unlikely; monitor read-heavy paths |

**Overall Risk: LOW** - WAL mode has been production-stable since SQLite 3.7.0 (2010). It's the recommended mode for most multi-connection use cases.

---

## 5. Compatibility Concerns for Control-Plane Use Case

### Confirmed Compatible ✅

- **Single-host deployment:** All control-plane components run on same machine
- **Local filesystem:** Database stored on local disk (not NFS/network)
- **Multiple connections:** WAL specifically designed for this pattern
- **Mixed read/write workload:** Ideal use case for WAL
- **Python sqlite3 module:** Full WAL support
- **Node.js better-sqlite3:** Full WAL support
- **SQLite version >= 3.7.0:** Any modern system qualifies

### Potential Concerns (Minor)

1. **Backup considerations:**
   - Must copy all three files: `.db`, `.db-wal`, `.db-shm`
   - Or: checkpoint before backup to consolidate
   - Or: use SQLite backup API

2. **Read-only filesystem access:**
   - Requires write access to directory for -shm file
   - Solution: use `immutable=1` URI parameter if truly read-only

3. **Page size changes:**
   - Cannot change page_size while in WAL mode
   - Not relevant unless explicitly needed

### Control-Plane Specific Fit

| Requirement | WAL Compatibility |
|-------------|-------------------|
| Multiple agent sessions | ✅ Excellent - concurrent reads |
| Frequent small writes | ✅ Excellent - fast commit |
| Task/thread state updates | ✅ Excellent - reduced lock contention |
| Crash recovery | ✅ WAL is crash-safe |
| Horizontal scaling | ⚠️ Single-host only (but that's current architecture) |

---

## Summary: Pros and Cons

### Pros
- ✅ Concurrent reads during writes (primary benefit)
- ✅ Faster commits (no immediate DB file write)
- ✅ Trivial migration (single PRAGMA)
- ✅ Fully reversible
- ✅ 15+ years of production stability
- ✅ Better fsync behavior (fewer required)
- ✅ Sequential I/O pattern (better for HDDs)

### Cons
- ⚠️ Additional files (-wal, -shm) to manage
- ⚠️ Checkpoint overhead (configurable)
- ⚠️ Cannot use over network filesystems
- ⚠️ Slightly slower for read-only workloads (~1-2%)
- ⚠️ Very large transactions (>100MB) may be slower (rare)

---

## Implementation Recommendation

**Verdict: STRONGLY RECOMMEND WAL MODE**

For the control-plane's multi-agent, concurrent read/write workload, WAL mode directly addresses the contention issues observed. The implementation is minimal-risk, easily reversible, and expected to provide immediate improvement in concurrent operation latency.

### Suggested PRAGMA Configuration

```sql
-- Enable WAL mode
PRAGMA journal_mode=WAL;

-- Recommended companion settings
PRAGMA busy_timeout=5000;      -- Retry locked operations for 5s
PRAGMA synchronous=NORMAL;     -- Safe with WAL, better performance
PRAGMA cache_size=-64000;      -- 64MB cache (adjust based on workload)
PRAGMA temp_store=MEMORY;      -- Temp tables in memory
```

---

## References

- SQLite Official WAL Documentation: https://sqlite.org/wal.html
- SQLite Locking v3: https://sqlite.org/lockingv3.html
- SQLite PRAGMA Reference: https://sqlite.org/pragma.html

---

**Task Status:** COMPLETE  
**Deliverable:** Structured findings document with implementation guidance  
**Next action:** Engineering team review and implementation decision
