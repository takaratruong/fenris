# Proactive Self-Improving Agent 🦞

The Proactive Self-Improving Agent is a high-level cognitive framework designed to turn AI agents from simple task-followers into **proactive partners**.

It accomplishes this by integrating two core architectural patterns:

1.  **Proactivity & Security (from 3.1.0)**: Using the **WAL (Write-Ahead Log)** protocol to ensure zero-loss memory across sessions and maintaining a **Working Buffer** to survive context flushes.
2.  **Self-Improvement (from 3.0.10)**: Utilizing **Event Hooks** for error detection and the **Learning Loop** to extract long-term wisdom from interactions.

## New Integrated Architecture

The combined framework eliminates redundancy in asset management and unifies automation into a single, cohesive loop.

### Memory Hierarchy

-   **`SESSION-STATE.md`**: The agent's "RAM". Contains active task data, user corrections, and specific values discovered during the current thread.
-   **`USER.md` / `SOUL.md`**: The agent's "Identity & Context". These files store who you are and how the agent should behave.
-   **`.learnings/`**: The agent's "Evolving Wisdom". Contains structured logs of failures (`ERRORS.md`), corrections (`LEARNINGS.md`), and feature requests (`FEATURE_REQUESTS.md`).

### The Proactive & Self-Improving Loop

This unified framework creates a continuous cycle of improvement:

1.  **Anticipation & WAL**: When a user mentions a preference ("I like blue"), the agent immediately writes it to `SESSION-STATE.md` (WAL) before continuing.
2.  **Proactive Check-ins**: At 60% context usage, the agent starts a **Working Buffer** in `memory/working-buffer.md` to capture raw exchanges.
3.  **Automatic Detection**: Integrated **Hooks** monitor tool outputs. If a command fails, the agent is notified via a system event to log the error.
4.  **Learning Extraction**: Periodically (or upon correction), the agent distills the buffer and error logs into the `.learnings/` folder.
5.  **Skill Solidification**: When a learning pattern repeats 3+ times, the agent uses the `extract` automation to create a permanent, dedicated skill file.

## Automation & Tools

A unified script, `scripts/automation.sh`, handles all major operations:

-   **`audit`**: Performs a security scan and verifies workspace integrity.
-   **`detect`**: (Hook-target) Detects failures in tool outputs.
-   **`extract`**: Rapidly generates a new skill from a learning.
-   **`init`**: Sets up the necessary folder structure and base files.

## How to Get Started

1.  Install the skill into your OpenClaw workspace.
2.  Run `scripts/automation.sh init` to prepare your memory folders.
3.  Answer the onboarding questions to populate `USER.md` and `SOUL.md`.
4.  Commit to the **WAL protocol**: Write first, respond second.

---

*"Every interaction is an opportunity to learn. Every failure is a blueprint for the next success."*
