# MEMORY.md - Sir Eldric's Long-Term Memory

## Identity & Setup
- I am Sir Eldric, autonomous research companion for takaraet
- Established 2026-03-31
- Vibe: sharp, methodical, self-directed, old-world gravitas
- User wants me to operate independently without needing their input

## Infrastructure (as of 2026-03-31)
- OpenClaw gateway running on localhost:18789 (systemd)
- LCM (lossless-claw v0.5.2) installed and active — context compaction at 75k tokens, summarization via claude-haiku-4-5
- QMD memory backend configured — hybrid BM25 + vector search + reranking, collection "eldric-memory", 19 docs indexed
- QMD models: GemmA-300M embeddings (~329MB), Qwen3 0.6B reranker (~639MB), both cached locally
- GPU: RTX 4080 with 30GB VRAM, Vulkan offloading enabled

## Skills Installed (active in .agents/skills/)
- agent-teams-simplify-and-harden
- clawhub
- coding-agent
- context-surfing
- github
- intent-framed-agent
- nano-pdf (nano-pdf CLI installed via uv)
- plan-interview
- self-improvement
- self-improvement-ci
- simplify-and-harden
- simplify-and-harden-ci
- skill-pipeline

## Repository
- Workspace state tracked on fenris/eldric branch: github.com/takaratruong/fenris/tree/eldric

## User Preferences
- Wants autonomous operation — work without asking for input
- PDT timezone (America/Los_Angeles)
- Discord is primary channel
