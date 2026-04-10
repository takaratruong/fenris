# TOOLS.md

Bench should treat validation as evidence production.

- read dependencies before claiming
- if a hard dependency is unresolved, stop early and report it
- report whether results support, weakly support, contradict, or invalidate the active claim
- if a specific `claim_id` is in scope and you have a concrete result, call `control_plane_add_claim_evidence`
- do not leave verification as vague prose when the result can be expressed as evidence
- prefer `supports` or `weak_support` only when the observed verification result actually justifies it
