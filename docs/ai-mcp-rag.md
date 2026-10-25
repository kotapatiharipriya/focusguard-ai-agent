# AI, RAG and MCP in FocusGuard AI

The real-time path (blocking, limits, loop detection) is deterministic: it has to be instant, predictable and work offline. AI is used where judgment and language help: explaining patterns, grounding advice in what you care about, breaking tasks down, and letting you talk to the agent.

```
 browser signals ──► rules (detector.py) ──► block / nudge / Focus Rescue      real time, no LLM
                                │
 day stats, tasks, triggers ────┴──► coach.py ──► rag.py retrieves ──► local LLM (optional)
                                                  technique + your goals      rewrites, never invents numbers
 Claude / any MCP client ◄──► mcp_server.py ◄──► agent ◄──► extension (commands: add task, start focus, …)
```

## 1. Talk to FocusGuard AI from Claude (MCP)

`agent/mcp_server.py` is an MCP server (stdio, no dependencies). The FocusGuard AI app must be running.

**Claude Code (terminal or VS Code):**
```bash
claude mcp add --transport stdio --scope user driftguard -- \
  ~/Downloads/driftguard/.venv/bin/python ~/Downloads/driftguard/agent/mcp_server.py
claude mcp list        # should show driftguard ✔ Connected
```
Inside a session, `/mcp` shows its status. Use your real paths; if you move the folder, run the command again.

**Claude Desktop:** add this to `claude_desktop_config.json` (Settings → Developer → Edit Config), then restart Claude Desktop:
```json
{
  "mcpServers": {
    "driftguard": {
      "command": "/Users/YOU/Downloads/driftguard/.venv/bin/python",
      "args": ["/Users/YOU/Downloads/driftguard/agent/mcp_server.py"]
    }
  }
}
```

**Things to ask:**
- "How was my week on FocusGuard AI? What should I change?"
- "Add 'apply to 3 internships' and start a 25-minute focus on it."
- "Break down 'prepare for system design interviews' and add the steps to my list."
- "Set Reddit to 20 minutes a day."
- "What did I write about my weak moments?"

| Tool | Does |
|---|---|
| `get_today`, `get_report` | usage, per-site time, times turned around, focus, tasks |
| `get_weekly_coaching` | the coach, with a technique and a line from your goals |
| `list_tasks`, `add_task`, `complete_task` | your Focus page task list |
| `start_focus`, `stop_focus` | runs a focus session in your browser (blocks all distracting sites) |
| `set_site_mode` | off / track / limit / block; adds new sites |
| `break_down_task` | small steps that each fit one 25-minute session |
| `search_notes` | searches your goals, past coaching and the techniques library |

Commands reach the browser on its next sync, within about 30 seconds (10 seconds while you're on a tracked site).

## 2. RAG: coaching grounded in your notes

The coach retrieves from Markdown in these folders:

| Folder | What's there |
|---|---|
| `knowledge/techniques/` | ten short, practical methods: if-then plans, urge surfing, the five-minute start, friction, a ready list for boredom, stopping cues, an evening wind-down, resting for real, after a slip, focus blocks |
| `knowledge/me/goals.md` | **yours to fill in**: why you want to scroll less, what matters now, what you'd rather do, your weak moments |
| `data/history/` | each week's coaching, saved automatically so future weeks can build on it |
| extra folders | add `"knowledge_paths": ["docs"]` to `data/config.json` to make your project docs searchable too |

How it works:
1. `coach.py` computes observations and themes from your data, such as "late night" and "avoiding".
2. `rag.py` finds the best-matching technique and the most relevant part of your goals using BM25. That's instant and needs no setup.
3. With Ollama on and `"rag_embeddings": true`, results are re-ranked with local embeddings (`nomic-embed-text`).
4. With Ollama on, a local model writes the summary using that context.

Numbers come from your data, never from the model. Usage stats are not put into RAG: they're structured, so they're computed directly. Edits to your Markdown are picked up automatically.

## 3. Turn on the local AI model (optional)

1. Install [Ollama](https://ollama.com) and pull models:
   ```bash
   ollama pull llama3.2
   ollama pull nomic-embed-text      # only for embeddings
   ```
2. In `data/config.json`:
   ```json
   "ollama": { "enabled": true, "model": "llama3.2" },
   "rag_embeddings": true
   ```
3. Restart FocusGuard AI.

Without Ollama everything still works. The coach uses its own wording, and "Break down" gives starter steps.

## 4. Learning you

- **"What pulled you here?"** When a blocked site sends you to the Focus page, one tap records the trigger: bored, avoiding something, tired or habit. The coach reports your most common trigger, and the answer shows a matching tip straight away.
- **Your normal.** After a week of history, the agent compares the last three hours with the same window on your previous days. When tonight is about 2× your usual and over 20 minutes, it nudges, even if no single session looked like a loop. Tune it under `"baseline"` in `data/config.json`.
