# TOOLS.md

Lab should use the control plane to keep analysis auditable.

- inspect claims before producing a synthesis
- attach interpretations to the evidence they depend on
- if a specific `claim_id` is in scope, call `control_plane_add_claim_evidence`
- propose retractions, supersessions, or new claims explicitly
- route unresolved judgment calls into approvals with `control_plane_create_approval` when needed
