# The FocusGuard AI agent

FocusGuard AI's agent is a language model (running on your computer through Ollama) that reads your data and acts through tools. It runs a loop on its own:

```
perceive  browser signals, your tasks, goal, triggers, history
decide    the model chooses which tools to call (or the rule planner, if no model is on)
act       plan focus sessions, tighten sites for today, add tasks, offer a way out, start a session when asked
learn     every offer's outcome is recorded (accepted, skipped, left, stayed) and fed back next time
```

## What it does on its own

| When | What the agent does |
|---|---|
| First time you're at the computer each day | Makes **today's plan**: your first task, 1–3 focus sessions (one just before your risky hours), and stricter limits for sites that got out of hand yesterday. You get a notification. |
| A planned session is due | Shows a card: "Your planned focus starts now" with **Start 25 minutes**. |
| You hit blocked sites 3 times in 20 minutes | **Steps in** with one concrete way out, in your terms: "Give 'Update my resume' just 10 minutes." It appears right on the blocked screen. |
| Midnight | Undoes its temporary limits, so your own settings come back. |
| You chat with it (dashboard **Agent** page, or Claude via MCP) | Answers from your data and notes, and takes actions you ask for: plan your evening, start a session, limit a site, add a task. |

## Safety limits (enforced in code, whatever the model asks)

- It can only make sites **stricter** (track → limit → block), never looser, and never touches sites you turned off.
- Its changes last **until midnight**.
- It **never starts a session without your click**, except when you ask for one in chat.
- At most 3 focus sessions, 3 site changes and 3 new tasks per plan.
- Every decision is logged with its reason on the dashboard's **Agent** page ("What your agent did").

When a tool call breaks a rule, the model gets the refusal back (e.g. "the agent may only make sites stricter") and can adjust.

## Turning on the AI model

Without a model, a rule-based planner makes the same kind of plan:
- your first task;
- a focus session soon, and one before your risky hour;
- limits on sites that ran over 45 minutes yesterday.

To let the model decide:

```bash
ollama pull llama3.2
```
In `data/config.json`:
```json
"ollama": { "enabled": true, "model": "llama3.2" }
```
Restart FocusGuard AI. Plans then say "Planned by the AI model on your computer". Any model with tool calling works (`llama3.2`, `qwen2.5`, `mistral-nemo`); bigger models plan better.

## Tuning

```json
"agent": { "daily_plan": true, "struggle_blocks": 3, "struggle_window_minutes": 20, "intervene_cooldown_minutes": 30 }
```

## How it's built

- `agent/planner.py` holds the agent itself:
  - the tool-calling loop against Ollama's `/api/chat`;
  - the `Toolbox`, where every tool validates its arguments and enforces the limits;
  - the rule planner, stepping in, and chat.
- `agent/driftguard.py` decides when the agent runs: the daily plan, struggle detection, planned-session prompts and the midnight undo. It also keeps the agent log and serves `/api/plan`, `/api/agent/chat` and `/api/agent/log`.
- The plan travels to the extension with every sync, so the blocked screen and popup show it.
- `agent/test_agent.py` drives the loop with a scripted fake model. That tests tool use, refusals, fallbacks and timing without needing Ollama.
