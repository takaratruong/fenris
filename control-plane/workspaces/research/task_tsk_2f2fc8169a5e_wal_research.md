# SQLite WAL Mode Research for Lock Contention

**Task:** tsk_2f2fc8169a5e  
**Thread:** thr_efdb9a9a4f69  
**Source:** Official SQLite documentation (sqlite.org/wal.html)

---

## 1. How WAL Changes the Locking Model

### Traditional Rollback Journal (DELETE mode)
- Writers **block all readers** during write transactions
- Readers **block writers** while reading
- Only one connection can write at a time
- Results in SQLITE_BUSY under concurrent load

### WAL Mode
- **Writers don't block readers** - readers see the last committed state from WAL
- **Readers don't block writers** - writes append to WAL while reads continue
- Multiple readers can proceed concurrently with one writer
- Lock contention dramatically reduced for mixed workloads

**Key mechanism:** Readers see the database state at the start of their read transaction (snapshot isolation). Writers append to the WAL file; readers check the WAL for relevant pages but don't block the writer.

---

## 2. When WAL Helps Most

### Best for: Read-Heavy Workloads with Some Writes
- Many concurrent readers + occasional writes = **big win**
- Read transactions never wait for writes to complete
- Example: web apps with lots of queries, few updates

### Also Good for: Mixed Read-Write
- Writers proceed while readers finish their snapshots
- No more "database is locked" errors from reader/writer conflicts

### Less Beneficial for: Write-Heavy Workloads
- Only **one writer at a time** (same as rollback journal)
- Writers still block other writers
- WAL file grows during heavy write bursts → checkpoint pressure
- If writes dominate, WAL overhead may not pay off

### Not Helpful for: Single-Threaded Access
- No concurrency benefit if only one connection exists
- Slight overhead from WAL machinery

---

## 3. Key Tradeoffs

### Checkpoint Behavior
- WAL file accumulates changes until checkpointed back to main DB
- **Auto-checkpoint** triggers at ~1000 pages by default (configurable)
- Checkpoint **can** block writers briefly in SQLITE_CHECKPOINT_RESTART/TRUNCATE modes
- Under heavy write load, WAL can grow large before checkpoint completes
- Passive checkpointing (`PRAGMA wal_checkpoint(PASSIVE)`) doesn't block but may not complete

### Memory Overhead
- WAL uses **shared memory** (`-shm` file) for coordination
- Additional memory for WAL index (wal-index)
- Typically small (~32KB per 1000 pages of WAL)

### Shared-Memory File Requirement
- Creates `-wal` and `-shm` files alongside database
- **Requires shared memory support** on the filesystem
- Doesn't work on network filesystems (NFS, etc.) without care
- All connections must access the same `-shm` file

### Other Considerations
- **Read performance:** Can be slightly slower for very large transactions (must check WAL)
- **Durability:** `synchronous=NORMAL` is safe with WAL (vs needing FULL in rollback)
- **Recovery:** WAL must be present for recovery; deleting `-wal` file can lose committed data

---

## 4. Implementation Complexity

### Enabling WAL: **Very Simple**
```sql
PRAGMA journal_mode=WAL;
```
- One-time command, persists in database
- Can be set at connection time or once per database
- Reversible: `PRAGMA journal_mode=DELETE;`

### Migration Considerations
- **Zero schema changes** required
- Existing code works without modification
- Test thoroughly with concurrent load before production

### Operational Overhead
- Monitor WAL file size (`-wal` file)
- May need explicit checkpointing under heavy write load
- Consider `PRAGMA wal_autocheckpoint=N;` tuning
- Ensure `-shm` and `-wal` files are backed up together with main DB

### Pitfalls to Watch
- **Don't delete the `-wal` file** while database is in use
- Network filesystems: shared memory issues
- Very long-running read transactions can prevent checkpointing

---

## Recommendation for Lock Contention

**Try WAL first.** Here's why:

1. **Implementation cost is near-zero** - single PRAGMA statement
2. **Risk is low** - easily reversible
3. **Directly addresses reader-writer blocking** - the most common lock contention scenario
4. **Proven technology** - default in many frameworks, battle-tested

### When WAL Won't Help
- If contention is **writer-writer** (multiple processes trying to write simultaneously), WAL doesn't help; only one writer at a time regardless of journal mode
- If using network filesystem without proper shared memory support

### Quick Test
```sql
-- Enable WAL
PRAGMA journal_mode=WAL;

-- Verify
PRAGMA journal_mode;  -- Should return 'wal'

-- Optional: tune checkpoint threshold
PRAGMA wal_autocheckpoint=1000;  -- pages (default)
```

---

## Summary Table

| Factor | Rollback (DELETE) | WAL Mode |
|--------|-------------------|----------|
| Readers block writers | Yes | **No** |
| Writers block readers | Yes | **No** |
| Writers block writers | Yes | Yes |
| Implementation effort | N/A | 1 line |
| Extra files | `-journal` (temp) | `-wal`, `-shm` |
| Network FS safe | Yes | Limited |
| Checkpoint needed | No | Yes (auto) |

**Bottom line:** For the memory-cache lock-contention issue, WAL is the lowest-risk, highest-reward first attempt.
