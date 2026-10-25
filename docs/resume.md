# Putting FocusGuard AI on your resume

## Project line

**FocusGuard AI: privacy-first AI agent that detects and interrupts doom-scrolling** · Python, JavaScript (Chrome Manifest V3), Streamlit, SQLite, RAG, MCP, Ollama

## Bullets (pick 3–4, and swap in your real numbers after a few weeks of use)

- Built an LLM agent (local model via Ollama tool calling) that plans each day, intervenes when the user repeatedly hits blocked sites, and acts on chat requests through a guarded toolbox. Tools validate arguments and enforce policy in code (stricter-only, expires at midnight, user-initiated sessions); refusals go back to the model, and a rule-based planner takes over when no model is available. Tested with a scripted fake model server.
- Built a local-first AI agent that perceives browsing behavior through a Chrome extension, detects doom-scrolling loops with deterministic rules (swipe pace, scroll velocity, app-hopping, compulsive reopening), and acts autonomously with in-page nudges, site blocking and timed focus sessions.
- Designed a two-tier architecture: real-time decisions run on fast, testable rules, and a local LLM (Ollama) is used only for reflective weekly coaching. That keeps latency low and makes the agent work offline.
- Implemented a Manifest V3 extension that catches full page loads and single-page-app navigation (e.g. YouTube Home → Shorts). It redirects blocked sites to a task-and-timer page, enforces per-site daily limits and keeps 90 days of usage stats.
- Built a stdlib-only Python service with validated batched event ingest, idempotent SQLite storage, session reconstruction, token pairing and origin checks. It runs as a login item on macOS, Windows and Linux with a system-tray UI.
- Privacy by design: URLs are reduced to coarse labels on the device before storage, the server binds to localhost only and drops unknown fields, and nothing leaves the user's machine.
- Added a RAG-grounded coach: BM25 retrieval (with optional local-embedding re-ranking) over a curated techniques library, the user's goals and past weekly summaries. A local LLM writes the advice from that context, while all numbers come from structured data.
- Exposed the agent through a Model Context Protocol (MCP) server, so Claude and other MCP clients can query usage, manage tasks, start focus sessions and change blocking rules. Commands are relayed to the browser extension through the local agent.
- Built per-user baselines (median of the same time window over the last 14 days) to flag unusually long evenings, and in-the-moment trigger capture (bored / avoiding / tired / habit) that feeds the coach.
- Built a Streamlit dashboard (Altair charts, weekday × hour drift heatmap, task management, RAG chat with source citations) that controls the browser extension through the local agent, with optimistic UI updates while commands sync.
- Designed a first-run onboarding (8 questions) that personalizes the agent: answers map to blocking rules, focus length and time-window protection, and become a profile document the RAG coach retrieves from.
- Wrote 46 unit and integration tests plus end-to-end browser tests in headless Chromium (Playwright) covering blocking, limits, focus sessions and nudge-to-rescue flows.
- *(After using it)* Reduced my own time on short-form video by X% over N weeks, measured with the tool's own reports.

## How to talk about it in an interview

- **Why rules and not an LLM for detection?** Detection runs every few seconds and must be predictable. An LLM would be slow, cost compute, and behave inconsistently. The model adds value where judgment and language matter: explaining patterns and suggesting a change.
- **What makes it an agent?** It runs a perceive → decide → act → learn loop without being asked:
  - perceive: browser signals
  - decide: rules
  - act: block, redirect, nudge, start a rescue
  - learn: it tracks outcomes (did you leave after the nudge?), and the coach adapts suggestions to your settings and history.
- **Hardest bug:** service workers in MV3 get suspended. State lives in `chrome.storage`, timers use `chrome.alarms`, and events queue offline and retry, so nothing is lost when the worker sleeps or the agent is down.
- **What you'd do next:** the phone (see `roadmap.md`), and an A/B comparison of nudge wordings using the outcome data.

## Show it

- Put the repo on GitHub with the README's architecture diagram and 3 screenshots: the Focus page after a block, the report and the nudge card.
- A 30-second screen recording (YouTube Home → tap Shorts → redirected to your tasks) explains the project faster than any bullet.
