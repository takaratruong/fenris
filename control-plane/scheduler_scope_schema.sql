-- Scheduler-Backed Engineer Scope Schema
-- Extension tables for governed jobs and scopes

-- Scopes define isolated execution contexts for engineer tasks
CREATE TABLE IF NOT EXISTS scopes (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    owner_agent TEXT DEFAULT 'engineer',
    config TEXT,  -- JSON: execution constraints, resource limits, etc.
    status TEXT DEFAULT 'active',  -- active, paused, archived
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Governed jobs are scheduled work items within a scope
CREATE TABLE IF NOT EXISTS governed_jobs (
    id TEXT PRIMARY KEY,
    scope_id TEXT NOT NULL,
    task_id TEXT,  -- Optional link to tasks table
    title TEXT NOT NULL,
    brief TEXT,
    job_type TEXT DEFAULT 'one-shot',  -- one-shot, recurring, triggered
    schedule_cron TEXT,  -- For recurring jobs (cron expression)
    schedule_interval_ms INTEGER,  -- For interval-based jobs
    next_run_at TEXT,  -- When the job should next execute
    last_run_at TEXT,
    assigned_worker TEXT,  -- Worker ID handling this job
    status TEXT DEFAULT 'pending',  -- pending, scheduled, running, completed, failed, paused
    priority INTEGER DEFAULT 0,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    timeout_ms INTEGER DEFAULT 300000,  -- 5 min default
    result TEXT,  -- JSON result payload
    error TEXT,
    created_at TEXT DEFAULT (datetime('now')),
    updated_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    FOREIGN KEY (scope_id) REFERENCES scopes(id),
    FOREIGN KEY (task_id) REFERENCES tasks(id)
);

-- Job runs track execution history
CREATE TABLE IF NOT EXISTS job_runs (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    worker_id TEXT,
    started_at TEXT DEFAULT (datetime('now')),
    completed_at TEXT,
    status TEXT DEFAULT 'running',  -- running, completed, failed, timeout
    output TEXT,  -- JSON execution output
    error TEXT,
    duration_ms INTEGER,
    FOREIGN KEY (job_id) REFERENCES governed_jobs(id)
);

-- Scheduler state for tracking global scheduler status
CREATE TABLE IF NOT EXISTS scheduler_state (
    id TEXT PRIMARY KEY DEFAULT 'global',
    status TEXT DEFAULT 'running',  -- running, paused, stopped
    last_tick_at TEXT,
    tick_interval_ms INTEGER DEFAULT 10000,  -- 10 second default tick
    updated_at TEXT DEFAULT (datetime('now'))
);

-- Indexes for efficient queries
CREATE INDEX IF NOT EXISTS idx_governed_jobs_scope ON governed_jobs(scope_id);
CREATE INDEX IF NOT EXISTS idx_governed_jobs_status ON governed_jobs(status);
CREATE INDEX IF NOT EXISTS idx_governed_jobs_next_run ON governed_jobs(next_run_at);
CREATE INDEX IF NOT EXISTS idx_job_runs_job ON job_runs(job_id);
CREATE INDEX IF NOT EXISTS idx_scopes_status ON scopes(status);
