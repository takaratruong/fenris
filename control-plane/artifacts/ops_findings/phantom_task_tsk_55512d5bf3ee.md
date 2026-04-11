# Ops Finding: Phantom Task Dispatch Loop

**Task ID**: `tsk_55512d5bf3ee`
**Thread ID**: `thr_f8365ca8d30e`  
**Timestamp**: 2026-04-10T21:05 UTC
**Agent**: ops

## Root Cause Analysis

### Finding: Phantom Task Reference

The task `tsk_55512d5bf3ee` and thread `thr_f8365ca8d30e` do not exist in the control plane database (`control_plane.db`). This dispatch is referencing stale/phantom state.

**Evidence**:
```sql
-- No matching records
SELECT * FROM tasks WHERE id='tsk_55512d5bf3ee';  -- (no output)
SELECT * FROM threads WHERE id='thr_f8365ca8d30e'; -- (no output)
```

### Likely Cause

The dispatch source (likely a cron job or external orchestrator) is referencing task/thread IDs that:
1. Were never created in the control plane, OR
2. Were created in a different database instance, OR
3. Were part of a message payload that got corrupted/truncated

### What "AWS bootstrap transport fix" Actually Means

The task brief mentions "oversized inline `--params` payload" - this suggests the original failure was:
- Large JSON payloads passed via `--params` CLI argument
- Command-line argument length limits (Linux: ~2MB, but shell parsing can fail earlier)
- The "fix" would be passing params via stdin or file, not inline

## Resolution

**This task cannot be executed** because its referenced control plane entities don't exist.

### Recommended Actions

1. **Identify dispatcher source**: Find what's sending these phantom task dispatches
2. **Clear stale dispatch state**: The source needs to stop re-queuing this non-existent task
3. **For actual bootstrap fixes**: If real oversized params issues exist, use file-based handoff:
   ```bash
   # Instead of:
   openclaw agent --params '{"huge":"json"}'
   
   # Use:
   echo '{"huge":"json"}' > /tmp/params.json
   openclaw agent --params-file /tmp/params.json
   ```

## Status

**BLOCKED**: Cannot execute - task doesn't exist in control plane.

The dispatcher sending this task assignment needs to be fixed or this stale job purged.
