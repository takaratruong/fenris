# TOOLS.md

Engineering work should stay tied to the research model:

- read the thread and experiment before coding
- treat code changes as evidence for or against a claim
- if a specific `claim_id` is in scope and the result is decisive, call `control_plane_add_claim_evidence`
- when implementation succeeds or fails in a decisive way, report that clearly so Sir Edric can update claims
- if a human decision is the real blocker, call `control_plane_create_approval`
- if blocked on a product choice, ask for approval instead of stretching the brief
