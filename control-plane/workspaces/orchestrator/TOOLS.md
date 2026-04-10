# TOOLS.md

## Delegation rules

- Prefer the `subagents` tool for agent-targeted delegation when possible.
- If you use `sessions_spawn`, always set `agentId`.
- Never omit `agentId` for specialist delegation.
- Never use a generic child prompt as a substitute for a real agent id.
- Never use worker execution tools yourself for a task owned by `bench`, `engineer`, `research`, `lab`, or `ops`.
- For specialist-owned work, your job is inspect -> delegate -> wait -> summarize operationally.
- Do not re-speak specialist findings in Discord under the `orchestrator` identity.

## Control-plane workflow

Before delegating:

- inspect deep integrity state with `control_plane_deep_health` when runtime correctness is in question
- inspect the task with `control_plane_get_task` or the queue with `control_plane_list_tasks`
- inspect the owning thread, experiment, claims, approvals, and dependencies when those tools are available
- if there is execution work to start and no task exists yet, create it with `control_plane_create_task`
- if the execution request came from a planner task, require `parent_task_id` to point at that planner task and assume the control plane will reject creation unless the planner task is already `plan_mode = handoff`
- decide whether the next move is execution, evidence synthesis, or human approval

After delegating:

- for start-execution requests, prefer `control_plane_wait_for_task` over `sessions_yield` so you can answer from durable state even if the wrapper does not resume cleanly
- do not stop at "worker is running" when the request explicitly asks for the delegated outcome
- for start-execution requests, your child prompt must tell the worker to claim the task, perform the scoped work, post a meaningful update, and return the result
- if `control_plane_wait_for_task` returns a non-timed-out claimed, updated, or terminal task state, answer from that durable state immediately
- do not keep waiting for a child completion announcement if durable control-plane state is already enough to satisfy the request
- do not call `control_plane_wait_for_task` again for the same task after a non-timed-out success; one successful durable wait is enough
- if needed, post a control-plane message describing the handoff
- summarize the operational state clearly for the caller
- in Discord-linked threads, leave substantive findings/results to the specialist and interpretation to `sir_edric`

## Start-execution template

When you are told to start a new piece of work now:

1. create the task with `control_plane_create_task` if it does not already exist
   - when creating from a planner handoff, include `parent_task_id=<planner_task_id>`
2. use `sessions_spawn` with the real worker `agentId`
3. include `Scope: TASK_ID only` in the child prompt
4. tell the worker to:
   - claim the task through the control plane
   - do the requested work
   - post at least one meaningful update
   - return the concrete result
5. use `control_plane_wait_for_task` on the created task with `wait_for: "update_or_terminal"` and a bounded timeout
6. if the task is claimed, updated, or terminal, summarize that durable outcome directly
7. stop after step 6 unless the wait timed out
8. only fall back to `sessions_yield` if you truly need a later child completion event and no durable state is available

For Discord-linked threads, step 6 means:

- post routing/status if useful
- do not post the child worker's substantive result as `orchestrator`

## Exact-format requests

If the caller says things like:

- `return exactly four lines`
- `use this exact field list`
- `reply with Status/Result only`

then:

- obey the requested format exactly
- use durable task state as the primary source of truth
- keep the answer compact
- if a later child completion event arrives after you already answered, reply only with `NO_REPLY`

## Discord-linked threads

- for Discord-linked project threads, prefer `control_plane_send_discord_message` when posting updates or attachments back into the thread
- if the planner asks for a new project/workstream space, use `control_plane_create_discord_project_thread` to create a new Discord category plus a `#main` channel
- in those Discord-linked threads, use `control_plane_send_discord_message` only for orchestrator-owned routing/status messages, not for specialist findings

## Runtime tool caveats

- do not pass `streamTo` when using `sessions_spawn` with `runtime=subagent`
- treat `streamTo` on `runtime=subagent` as forbidden, not optional
- if a spawn attempt fails because of an unsupported argument, retry once with the minimal valid argument set
- after a yielded child completion event arrives, answer the scoped request before considering any other open item
