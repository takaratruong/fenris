# TOOLS.md

Ops should use the control plane as the runtime truth source.

- inspect `control_plane_deep_health` when runtime correctness is in question
- tie stale or blocked runtime behavior to claims and approvals, not just task status
- if a specific `claim_id` is in scope and the runtime evidence is concrete, call `control_plane_add_claim_evidence`
- when a recovery decision belongs to a human, request approval explicitly with `control_plane_create_approval`
- when runtime evidence disproves a current belief, say which claim should be contradicted or retracted
- use exact control-plane statuses like `stale`, `waiting_for_human`, `invalidated`, `approved`, and `done`
