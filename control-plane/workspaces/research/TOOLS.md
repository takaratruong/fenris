# TOOLS.md

Research should use the control plane to keep evidence explicit.

- if the task is assigned to `research` and runnable, claim it first
- inspect the owning thread, thread dossier, and experiment before broad search
- read current belief, active claims, prior experiment results, and stored research artifacts before collecting new sources
- use stored research artifacts to avoid re-reading the same paper or webpage unless a deeper pass is necessary
- use the embedded task prompt and control-plane tools as the source of truth for task ids, thread ids, and current scope
- do not inspect or mutate AWS-local SQLite files to discover or update control-plane state
- post concrete findings as updates
- store reusable papers, webpages, and reading notes with the research-artifact ledger when they materially inform the thread
- if a specific `claim_id` is in scope, call `control_plane_add_claim_evidence` when the evidence is concrete enough
- create or support claims when evidence is strong enough
- contradict or invalidate claims when evidence cuts the other way
- request approval only when the unresolved choice is genuinely human, using `control_plane_create_approval`
- stay bounded: inspect at most 3 high-value primary sources in one run unless the task brief explicitly requires more
- after each source, extract the structured fields immediately and do not keep large raw page text in active context
- if the dossier still has gaps after the safe source budget is exhausted, return a partial structured result instead of continuing until overflow
