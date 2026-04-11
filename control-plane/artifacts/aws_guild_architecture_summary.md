# AWS Guild Architecture Summary

## Overview

The AWS guild operates as a multi-agent system coordinated through a SQLite-based control plane. Work flows through threads → tasks → claims, with specialized workers handling different domains.

---

## Agent Tree

```
                    ┌─────────────────┐
                    │   orchestrator  │
                    │  (task router)  │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│   research    │   │   engineer    │   │     bench     │
│  (evidence)   │   │(implementation)│   │(verification) │
└───────────────┘   └───────────────┘   └───────────────┘
        │                    │                    │
        ▼                    ▼                    ▼
┌───────────────┐   ┌───────────────┐   ┌───────────────┐
│      lab      │   │      ops      │   │chief_of_staff │
│  (analysis)   │   │   (runtime)   │   │  (synthesis)  │
└───────────────┘   └───────────────┘   └───────────────┘
```

### Worker Roles

| Agent | Role | Specialty |
|-------|------|-----------|
| `orchestrator` | Task router | Delegates to specialists, synthesizes results |
| `research` | Evidence gathering | Investigation, dossier management, claim framing |
| `engineer` | Implementation | Code, debugging, technical execution |
| `bench` | Verification | Benchmarks, validation, claim evidence |
| `lab` | Analysis | Synthesis, interpretation, conclusions |
| `ops` | Runtime health | Recovery, robustness, operational state |
| `chief_of_staff` | System synthesis | Cross-thread coherence, decision framing |

---

## Control Plane Schema

**Core tables:**

- **threads** — Top-level work containers (active/completed)
- **tasks** — Individual work items, linked to threads, support hierarchy via `parent_task_id`
- **claims** — Agent ownership of tasks with heartbeat tracking
- **task_updates** — Progress log per task
- **artifacts** — Output files linked to tasks or threads

**Current state:** 5 active threads, 22 completed tasks, 1 running task.

---

## Task Flow

```
1. Thread created (goal framed)
        │
        ▼
2. Tasks spawned (assigned_to = worker)
        │
        ▼
3. Worker claims task (claims table)
        │
        ▼
4. Worker posts updates (task_updates)
        │
        ▼
5. Worker produces artifacts (artifacts table)
        │
        ▼
6. Task transitions (pending → running → completed/blocked)
```

**Key patterns:**
- Claims include session_id and heartbeat for liveness tracking
- Artifacts can be task-scoped or thread-scoped
- Parent-child task relationships enable decomposition

---

## Workspace Layout

```
control-plane/
├── control_plane.db      # SQLite state
├── schema.sql            # Table definitions
├── artifacts/            # Output files
└── workspaces/
    ├── orchestrator/     # Routing agent
    ├── research/         # Evidence specialist
    ├── engineer/         # Implementation specialist
    ├── bench/            # Verification specialist
    ├── lab/              # Analysis specialist
    ├── ops/              # Runtime specialist
    └── chief_of_staff/   # System synthesizer
```

Each workspace contains `AGENTS.md` (role definition), `TOOLS.md`, `SOUL.md`, and task-specific artifacts.

---

## Observed Agent IDs in Task History

Primary workers: `research`, `engineer`, `bench`, `lab`, `ops`, `chief_of_staff`  
Test/stress workers: `dr_fenris`, `main`, `test_worker`, `worker_b`, `worker_c`

---

*Generated: 2026-04-10 17:31 UTC | Task: tsk_33f48ceb1ac6*
