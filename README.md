# FocusGuard AI

This project was previously named DriftGuard. Internal database and
environment-variable names remain compatible so existing personal data can be
migrated safely.

## Deployed App: 
https://focusguard-ai-agent.streamlit.app/
## Public repository scope

This repository contains the actual implementation and a privacy-safe demo:

- `agent/` — autonomous runtime, deterministic detectors, planner, guarded tools, recovery, and audit logs
- `agent/rag.py` + `knowledge/` — local retrieval over techniques, goals, and coaching history
- `agent/mcp_server.py` — MCP server exposing approved read/action tools
- `extension/` — Chrome Manifest V3 sensor and intervention layer
- `app/` — Streamlit control center and reports
- `demo/` — synthetic-data showcase that can be deployed publicly
- `agent/test_*.py` — automated tests for the agent, RAG, MCP, detector, and collector

The runtime `data/` directory is intentionally empty in Git. It is created on
the user's machine and is excluded by `.gitignore`.

## Run the public showcase

```bash
cd demo
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

This showcase does not connect to the private local agent. It demonstrates the
same agent concepts with synthetic browsing signals.

A personal AI agent that stops doom-scrolling and turns you back to your own tasks. Everything runs on your computer.

**What it does**

- **An AI agent that plans and steps in.** Each morning it plans your day: your first task, focus sessions placed before your risky hours, and tighter limits for sites that ran over yesterday. When you keep reaching for blocked sites it steps in with one concrete way out. You can chat with it and it takes actions. It can only make blocking stricter, only until midnight, and logs every decision. See `docs/agent.md`.

- **Gets to know you first.** A one-minute setup asks which sites to block, when you lose time, what pulls you in, your goal and tasks, what you'd rather do instead, and whether to allow exceptions. It sets everything up from those answers.
- **Protects your risky hours.** During the hours you choose (say 10 PM to 1 AM), every tracked site is closed, not just the blocked ones.
- **Blocks the sites you choose.** Every site is Off, Track, Limit (minutes a day) or Block. YouTube Shorts, Instagram Reels and TikTok start on Block. You can add any website.
- **Stops you at the door, then gets you out.** Opening a blocked site (or jumping to Shorts from the YouTube home page) shows a clear "You blocked Instagram" screen with three one-click ways out: focus on your top task, 10 minutes of something from your own "instead" list, or a guided 2-minute reset.
- **Locks the door while you focus.** During a focus session every distracting site is blocked, including ones you normally only track.
- **Notices loops before you do.** The background agent watches swipe pace, endless scrolling, app-hopping and reopening. It steps in with a card that explains what's happening, plus Focus Rescue.
- **Measures drift, not just minutes.** Every active session gets a transparent 0–100 Drift Level based on duration, feed intensity, short-video pace, app switching and quick returns. The dashboard and intervention show the reasons behind the score.
- **Checks intention against outcome.** If you said you want time for a goal but are spending it on an endless-feed surface, the agent explains the mismatch and offers a concrete next action without reading page titles, searches or video content.
- **Learns which intervention works for you.** After a few responses, it uses your exits and completed rescues to personalize the next Rescue length and explains what it learned. It starts conservatively when there is not enough history.
- **Shows the truth.** The report has time per site, how often you were turned around, focus time and tasks done for today, 7 or 30 days.
- **Coaches you from your own words.** A RAG-grounded weekly coach draws on your goals file, a library of proven techniques and your past weeks. An optional local LLM writes it, and it never makes up numbers.
- **Learns your triggers and your normal.** "What pulled you here?" records why you reached for a feed, and a personal baseline flags evenings that run long *for you*.
- **Breaks tasks down** into 25-minute steps when a task feels too big to start.
- **A dashboard for the big picture.** A Streamlit app shows today, trends, a "when you drift" heatmap, your tasks, a coach you can chat with, and your site rules. Everything you change there reaches Chrome within seconds.
- **Talk to it from Claude.** An MCP server lets Claude Desktop or Claude Code report your week, add tasks, start focus sessions and change site rules. See `docs/ai-mcp-rag.md`.
- **Optional OpenAI reasoning.** The local agent can use OpenAI for planning and tool selection when you explicitly set `OPENAI_API_KEY` and enable it in `data/config.json`. Rules and safety limits remain local; without that setting it runs locally with rules/Ollama.

## Quick start

1. **Extension:** in `chrome://extensions`, turn on Developer mode, click **Load unpacked** and choose `extension/`. Settings open so you can pick your sites. The Streamlit dashboard also has a **Download Chrome extension** button that packages this folder for installation.
2. **Agent (adds nudges, the coach and the dashboard):**
   ```bash
   python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
   .venv/bin/python agent/driftguard.py              # run now
   .venv/bin/python agent/autostart.py install       # or: start at every login
   ```
   The extension pairs with it automatically.
3. **Dashboard:** click **Dashboard** in the extension popup or **Open dashboard** in the menu-bar icon, or run
   `.venv/bin/streamlit run app/dashboard.py`. It opens at http://localhost:8501 with Today, Connect, Agent, Report, Tasks, Coach and Sites. Use **Connect** to download and add the Chrome extension.

If you are upgrading from DriftGuard, copy your existing `data/` directory
into this project before starting it. Do not share `data/.collector_token`.

4. **Diagnostics:** run `.venv/bin/python agent/doctor.py` whenever you install, update, or restart the computer. The production acceptance checklist is in `docs/production.md`.

The dashboard's sidebar includes the extension download and install instructions. The extension is not a cloud add-on: after unzipping it, Chrome loads it locally through Developer mode, while the local agent and dashboard communicate over `127.0.0.1`.

## How it's built

```
 Chrome extension (Manifest V3)                    Local agent (Python, stdlib)
 ─────────────────────────────                    ─────────────────────────────
 sites.js      site rules → (app, surface)         collector.py  validated event ingest → SQLite
 background.js perceive: attention sessions,       detector.py   deterministic loop rules
               swipes, scroll bursts               driftguard.py decide + act: nudges, Focus Rescue,
               act: block / redirect / focus                     outcome tracking, control state
               sessions / passes / commands        coach.py      weekly insights + rag.py retrieval
 focus, report, popup, settings pages                            (BM25 / local embeddings), local LLM
 daily stats in chrome.storage                     mcp_server.py MCP tools for Claude and other clients
 state + tasks shared with the agent               tray.py, notify.py, autostart.py
            │  batched events + replies (nudges, pause/rescue state)  ▲
            └────────────── 127.0.0.1 only, token-paired ─────────────┘
```

Design decisions:
- **Rules decide in real time; the LLM only explains.** Blocking and nudges are deterministic, fast and predictable offline. The model is used for reflection, where latency doesn't matter.
- **Private by construction.** URLs become coarse labels (`youtube_shorts`, `feed`) before anything is stored. The backend's validator drops unknown fields, binds to localhost and refuses web-page origins.
- **Works without the agent.** The extension is a complete product on its own. The agent adds the parts that need a process running all the time.

## Layout

| Path | What |
|---|---|
| `extension/` | Chrome extension: blocking, Focus page, report, settings, nudge card |
| `app/dashboard.py` | Streamlit dashboard: today, reports with heatmap, tasks, coach chat (RAG), site rules |
| `agent/driftguard.py` | the always-on agent: server, rules loop, alerts, rescue state, status page |
| `agent/doctor.py` | installation, database, permissions, and agent health diagnostics |
| `agent/collector.py` | event ingest, validation, SQLite, session reconstruction |
| `agent/detector.py` | loop-detection rules (tune in `data/config.json`) |
| `agent/planner.py` | the AI agent: tool-calling loop, guarded tools, rule planner |
| `agent/coach.py`, `agent/rag.py` | weekly coach, retrieval over `knowledge/` |
| `agent/mcp_server.py` | MCP server for Claude Desktop / Claude Code |
| `knowledge/` | techniques library and your goals file (`knowledge/me/goals.md`) |
| `agent/tray.py`, `notify.py`, `autostart.py` | menu-bar icon, notifications, start at login |
| `docs/` | how the agent behaves, event schema, roadmap, resume notes |

Tests: `python3 -m unittest discover -s agent -p "test_*.py"` (52 tests, including drift scoring, adaptive intervention learning, the agent's tool loop against a scripted model, MCP over real stdio, and the dashboard).
