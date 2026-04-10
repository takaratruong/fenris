# SQLite Settings Rollback Procedure

## Scenario
Rolling back from modified SQLite settings to baseline production settings.

## Pre-Rollback Checklist
- [ ] Stop all processes accessing the database
- [ ] Create backup: `cp database.sqlite database.sqlite.bak`
- [ ] Verify backup integrity: `sqlite3 database.sqlite.bak "PRAGMA integrity_check;"`

## Rollback Steps

### 1. Rollback Journal Mode (WAL → DELETE or vice versa)

```bash
# To restore WAL mode:
sqlite3 database.sqlite "PRAGMA journal_mode=wal;"

# To restore DELETE mode:
sqlite3 database.sqlite "PRAGMA journal_mode=delete;"
```

### 2. Rollback Connection-Level Pragmas
These don't persist, so rollback means updating application code to apply correct settings on connection:

```sql
-- Production defaults for OpenClaw
PRAGMA synchronous=FULL;    -- 2
PRAGMA cache_size=-2000;    -- 2MB
PRAGMA busy_timeout=5000;   -- 5 seconds
```

### 3. Rollback Page Size (requires VACUUM)

```bash
# Change page size (costly operation on large DBs)
sqlite3 database.sqlite "PRAGMA page_size=4096; VACUUM;"
```

### 4. Verify Integrity After Rollback

```bash
sqlite3 database.sqlite "PRAGMA integrity_check;"
# Expected output: "ok"
```

### 5. Full WAL Checkpoint (if using WAL)

```bash
sqlite3 database.sqlite "PRAGMA wal_checkpoint(TRUNCATE);"
```

## Emergency Recovery

If database corruption occurs:

```bash
# Export and reimport
sqlite3 corrupt.db ".dump" > backup.sql
sqlite3 new.db < backup.sql
```
