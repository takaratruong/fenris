# SQLite Pragma Persistence Test Results

## Test Environment
- SQLite Version: 3.45.1
- Test Date: 2026-04-10
- Test DB: /tmp/sqlite-pragma-test/test.db

## Settings Applied vs. What Persists

| Pragma | Value Set | Persists After Close? | Default After Reopen |
|--------|-----------|----------------------|---------------------|
| `journal_mode` | DELETE | ✅ YES | (keeps DELETE) |
| `synchronous` | NORMAL (1) | ❌ NO | 2 (FULL) |
| `cache_size` | -4000 | ❌ NO | -2000 (default) |
| `temp_store` | MEMORY (2) | ❌ NO | 0 (DEFAULT) |
| `mmap_size` | 268435456 | ❌ NO | 0 |
| `auto_vacuum` | INCREMENTAL | ❌ NO* | 0 (NONE) |
| `busy_timeout` | 5000 | ❌ NO | 0 |
| `page_size` | 4096 | ✅ YES | (stored in DB header) |

*Note: auto_vacuum requires setting BEFORE creating tables, or using VACUUM after change.

## Key Findings

### Pragmas That PERSIST (stored in DB file/header):
1. **journal_mode** - Stored in DB, persists
2. **page_size** - Stored in DB header, persists
3. **auto_vacuum** - Stored in DB, but must be set before first table creation

### Pragmas That DO NOT PERSIST (connection-level only):
1. **synchronous** - Must be set on each connection
2. **cache_size** - Must be set on each connection  
3. **temp_store** - Must be set on each connection
4. **mmap_size** - Must be set on each connection
5. **busy_timeout** - Must be set on each connection

## Implications for OpenClaw

The current production DB uses:
- `journal_mode=wal` (persisted)
- `synchronous=FULL` (needs per-connection)
- `cache_size=-2000` (needs per-connection)

If changing these settings in application code:
- **journal_mode changes** will persist automatically
- **All other performance pragmas** must be applied on every DB open

## Rollback Steps

See ROLLBACK.md for detailed rollback procedures.
