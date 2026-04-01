# Learnings

Corrections, insights, and knowledge gaps extracted from sessions.

Format:
```
## [Date] Topic
**Trigger:** What happened
**Learning:** What I learned
**Action:** What changed (file updated, pattern added, etc.)
```

---

## 2026-03-31 Cron Continuations

**Trigger:** Repeatedly forgot to set continuation crons after asking blocking questions. Work stalled for 7+ hours multiple times.

**Learning:** Writing a rule in AGENTS.md doesn't make me follow it. I understand the rule, agree with it, and still don't execute. The problem is behavioral, not informational.

**Action:** 
- Installed proactive-self-improving-agent skill
- Need to implement WAL-style "write before responding" pattern for cron setup
- The cron should be created in the SAME tool call block as the message, not as a separate "remember to do this"
