"""FocusGuard AI MCP server: talk to your anti-doom-scrolling agent from Claude
(Claude Desktop, Claude Code in VS Code) or any other MCP client.

    "How was my week?"            -> get_report / get_weekly_coaching
    "Add 'finish resume' to my tasks and start 25 minutes on it"
                                  -> add_task, start_focus
    "Block Instagram for good"    -> set_site_mode
    "What did I write about my weak moments?" -> search_notes

Runs over stdio (the client starts it), stdlib only. It talks to the running
FocusGuard AI app on 127.0.0.1, which relays commands to the browser extension.

Register with Claude Code:
    claude mcp add driftguard -- /path/to/.venv/bin/python /path/to/agent/mcp_server.py
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE = os.environ.get("DRIFTGUARD_URL", "http://127.0.0.1:8765").rstrip("/")
TOKEN_FILE = Path(os.environ.get("DRIFTGUARD_TOKEN_FILE", ROOT / "data" / ".collector_token"))
SERVER_INFO = {"name": "driftguard", "version": "0.4.0"}
SUPPORTED_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"]

NOT_RUNNING = ("The FocusGuard AI app isn't running, so I can't reach your data. "
               "Start it with `python3 agent/driftguard.py` (or install it to start at login) and try again.")


class AppError(Exception):
    pass


def _token() -> str:
    tok = os.environ.get("DRIFTGUARD_TOKEN")
    if tok:
        return tok
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        raise AppError(NOT_RUNNING)


def call_app(method: str, path: str, body: dict | None = None) -> dict:
    req = urllib.request.Request(
        BASE + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-DriftGuard-Token": _token()})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            msg = json.loads(e.read()).get("error", "")
        except Exception:
            msg = ""
        raise AppError(f"The FocusGuard AI app refused that ({e.code}{': ' + msg if msg else ''}).")
    except (urllib.error.URLError, OSError):
        raise AppError(NOT_RUNNING)


# ---------------------------------------------------------------------------
# Formatting helpers

def fmt(ms: float) -> str:
    m = round((ms or 0) / 60000)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def pl(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else ("es" if word.endswith("s") else "s"))


def day_key(offset: int = 0) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(time.time() - offset * 86400))


def get_state() -> dict:
    st = call_app("GET", "/api/state")
    if not st or not st.get("days") and not st.get("tasks"):
        raise AppError("The FocusGuard AI app is running but hasn't heard from the browser extension yet. "
                       "Open Chrome with the FocusGuard AI extension and try again in a minute.")
    return st


def labels(st: dict) -> dict:
    return {r["id"]: r["label"] for r in st.get("rules") or []}


def freshness(st: dict) -> str:
    age = (time.time() * 1000 - (st.get("updated_at") or 0)) / 60000
    return "" if age < 2 else f"\n(Browser data as of {round(age)} minutes ago.)"


def summarize(st: dict, keys: list[str]) -> dict:
    lab = labels(st)
    days = st.get("days") or {}
    t = {"distract": 0, "focus": 0, "pomodoros": 0, "tasks": 0, "turned": 0, "sites": {}}
    for k in keys:
        d = days.get(k) or {}
        for a, s in (d.get("apps") or {}).items():
            x = t["sites"].setdefault(lab.get(a, a), {"ms": 0, "visits": 0, "shorts": 0, "turned": 0})
            x["ms"] += s.get("ms", 0); x["visits"] += s.get("visits", 0); x["shorts"] += s.get("shorts", 0)
            t["distract"] += s.get("ms", 0)
        for a, n in (d.get("blocked") or {}).items():
            t["sites"].setdefault(lab.get(a, a), {"ms": 0, "visits": 0, "shorts": 0, "turned": 0})["turned"] += n
            t["turned"] += n
        t["focus"] += d.get("focusMs", 0); t["pomodoros"] += d.get("pomodoros", 0); t["tasks"] += d.get("tasksDone", 0)
    return t


def site_lines(t: dict) -> list[str]:
    rows = sorted(t["sites"].items(), key=lambda x: -x[1]["ms"])
    out = []
    for name, s in rows:
        if s["ms"] < 30000 and not s["turned"]:
            continue
        extra = []
        if s["shorts"]:
            extra.append(f"{s['shorts']} shorts")
        if s["turned"]:
            extra.append(f"turned around {s['turned']}×")
        out.append(f"- {name}: {fmt(s['ms'])}, {s['visits']} visits" + (f" ({', '.join(extra)})" if extra else ""))
    return out or ["- Nothing on tracked sites."]


def find_open_task(st: dict, ref: str) -> dict | None:
    r = ref.lower().strip()
    open_ = [t for t in st.get("tasks") or [] if not t.get("done")]
    return next((t for t in open_ if t["id"] == ref), None) or next((t for t in open_ if t["title"].lower() == r), None) \
        or next((t for t in open_ if r in t["title"].lower()), None)


# ---------------------------------------------------------------------------
# Tools

def t_get_today(args):
    st = get_state()
    t = summarize(st, [day_key()])
    lines = [f"Today: {fmt(t['distract'])} on distracting sites, focused {fmt(t['focus'])}, "
             f"turned around {pl(t['turned'], 'time')}, {pl(t['tasks'], 'task')} done."]
    lines += site_lines(t)
    f = st.get("focus")
    if f and not f.get("completedAt") and f.get("until", 0) > time.time() * 1000:
        left = round((f["until"] - time.time() * 1000) / 60000)
        task = next((x["title"] for x in st.get("tasks") or [] if x["id"] == f.get("taskId")), None)
        lines.append(f"A {f['kind']} session is running: {left} min left" + (f" on “{task}”." if task else "."))
    status = call_app("GET", "/api/status")
    ctl = status.get("control", {})
    if ctl.get("paused_until"):
        lines.append("FocusGuard AI is paused.")
    nudges = [a for a in status.get("alerts", [])]
    if nudges:
        lines.append(f"Nudges today: {len(nudges)}. Latest: {nudges[0]['headline']} ({nudges[0].get('outcome') or 'no answer yet'}).")
    return "\n".join(lines) + freshness(st)


def t_get_report(args):
    n = max(1, min(30, int(args.get("days", 7))))
    st = get_state()
    keys = [day_key(i) for i in range(n)]
    prev = [day_key(i) for i in range(n, 2 * n)]
    t = summarize(st, keys)
    p = summarize(st, prev)
    head = f"Last {n} days: {fmt(t['distract'])} on distracting sites"
    if p["distract"] > 60000:
        ch = round((t["distract"] - p["distract"]) / p["distract"] * 100)
        head += f" ({abs(ch)}% {'less' if ch < 0 else 'more'} than the {n} days before)"
    lines = [head + ".",
             f"Focused {fmt(t['focus'])} in {pl(t['pomodoros'], 'finished session')}; {pl(t['tasks'], 'task')} done; "
             f"FocusGuard AI turned you around {pl(t['turned'], 'time')}.", "", "By site:"] + site_lines(t)
    if n > 1:
        lines += ["", "By day (distracting / focused):"]
        days = st.get("days") or {}
        for k in reversed(keys):
            d = days.get(k) or {}
            dm = sum(s.get("ms", 0) for s in (d.get("apps") or {}).values())
            lines.append(f"- {k}: {fmt(dm)} / {fmt(d.get('focusMs', 0))}")
    return "\n".join(lines) + freshness(st)


def t_get_weekly_coaching(args):
    r = call_app("POST", "/api/coach", {})
    lines = [r.get("headline", ""), ""] + [f"- {i}" for i in r.get("insights", [])]
    if r.get("suggestion"):
        lines += ["", f"Suggestion: {r['suggestion']}"]
    if r.get("technique"):
        tq = r["technique"]
        lines += ["", f"Technique: {tq['title']}. {tq['how']}", f"(from {tq['source']})"]
    if r.get("goal"):
        lines += ["", f"From your goals ({r['goal']['section']}): {r['goal']['text']}"]
    return "\n".join(lines)


def t_list_tasks(args):
    st = get_state()
    tasks = st.get("tasks") or []
    open_ = [t for t in tasks if not t.get("done")]
    lines = [f"{pl(len(open_), 'open task')}:"] if open_ else ["No open tasks."]
    for t in open_:
        indent = "  " if t.get("parentId") else ""
        extra = f" — {t['pomodoros']} focus sessions" if t.get("pomodoros") else ""
        lines.append(f"{indent}- {t['title']}{extra}")
    if args.get("include_done"):
        done = [t for t in tasks if t.get("done")]
        if done:
            lines += ["", "Done recently:"] + [f"- {t['title']}" for t in done[-15:]]
    return "\n".join(lines) + freshness(st)


def t_add_task(args):
    title = " ".join(str(args.get("title", "")).split())
    if not title:
        raise AppError("A task needs a title.")
    call_app("POST", "/api/command", {"type": "add_task", "args": {"title": title[:140]}})
    return f"Added “{title}”. It will appear on your Focus page within about 30 seconds."


def t_complete_task(args):
    ref = str(args.get("task", "")).strip()
    st = get_state()
    t = find_open_task(st, ref)
    if not t:
        raise AppError(f"I couldn't find an open task matching “{ref}”. Use list_tasks to see them.")
    call_app("POST", "/api/command", {"type": "complete_task", "args": {"id": t["id"]}})
    return f"Marked “{t['title']}” as done. Nice."


def t_start_focus(args):
    minutes = max(5, min(120, int(args.get("minutes", 25))))
    task = str(args.get("task", "") or "").strip()
    call_app("POST", "/api/command", {"type": "start_focus", "args": {"minutes": minutes, "task": task or None}})
    return (f"Starting a {minutes}-minute focus session" + (f" on “{task}”" if task else "") +
            ". Your browser opens the Focus page and blocks every distracting site until it ends "
            "(it can take up to 30 seconds to kick in).")


def t_stop_focus(args):
    call_app("POST", "/api/command", {"type": "stop_focus", "args": {}})
    return "Stopping the current focus session."


def t_set_site_mode(args):
    site = str(args.get("site", "")).strip()
    mode = args.get("mode")
    if not site or mode not in ("off", "track", "limit", "block"):
        raise AppError("Give a site and a mode: off, track, limit or block.")
    a = {"site": site, "mode": mode}
    if mode == "limit":
        a["limit_minutes"] = max(1, min(600, int(args.get("limit_minutes", 15))))
    call_app("POST", "/api/command", {"type": "set_site_mode", "args": a})
    what = {"off": "ignored", "track": "tracked but never blocked", "block": "blocked",
            "limit": f"limited to {a.get('limit_minutes')} minutes a day"}[mode]
    return f"{site} will be {what}. Unknown websites are added to your list."


def t_break_down_task(args):
    task = str(args.get("task", "")).strip()
    if not task:
        raise AppError("Which task should I break down?")
    r = call_app("POST", "/api/breakdown", {"task": task})
    steps = r.get("steps") or []
    lines = [f"Steps for “{task}”:"] + [f"{i}. {s}" for i, s in enumerate(steps, 1)]
    if r.get("source") == "template":
        lines.append("(Starter steps. Turn on Ollama in data/config.json for steps written by your local AI model, "
                     "or rewrite them yourself and add them with add_task.)")
    if args.get("add_to_list"):
        call_app("POST", "/api/command", {"type": "add_task", "args": {"title": task[:140]}})
        for s in steps:
            call_app("POST", "/api/command", {"type": "add_task", "args": {"title": s, "parent": task[:140]}})
        lines.append("Added them to your task list.")
    return "\n".join(lines)


def t_search_notes(args):
    q = str(args.get("query", "")).strip()
    if not q:
        raise AppError("What should I search for?")
    from urllib.parse import quote
    res = call_app("GET", "/api/notes?q=" + quote(q)).get("results", [])
    if not res:
        return f"Nothing in your notes matches “{q}”. Your goals live in knowledge/me/goals.md."
    out = []
    for r in res:
        where = r["title"] + (f" › {r['section']}" if r["section"] else "")
        out.append(f"[{r['kind']}] {where} ({r['source']})\n{r['excerpt']}")
    return "\n\n".join(out)


def _plan_text(p):
    if not p or not p.get("message"):
        return "No plan for today yet."
    lines = [p["message"]]
    if p.get("first_task"):
        lines.append(f"First task: {p['first_task']}")
    for b in p.get("focus_blocks") or []:
        lines.append(f"- Focus {b['time']} for {b['minutes']} min" + (f" on {b['task']}" if b.get("task") else "") + f" ({b.get('status')})")
    for c in p.get("site_changes") or []:
        lines.append(f"- {c['label']}: {c['to']}" + (f" {c['limit_minutes']} min" if c.get("limit_minutes") else "") + " until midnight")
    lines.append(f"(planned by {'the local AI model' if p.get('source') == 'ollama' else 'rules'})")
    return "\n".join(lines)


def t_get_plan(args):
    p = call_app("GET", "/api/plan")
    return _plan_text(p if p.get("date") == day_key() else None)


def t_plan_my_day(args):
    return _plan_text(call_app("POST", "/api/plan", {}))


S = lambda props, req=(): {"type": "object", "properties": props, "required": list(req), "additionalProperties": False}
RO = {"readOnlyHint": True, "openWorldHint": False}
RW = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": False}

TOOLS = [
    ("get_today", "How today is going: time on distracting sites per site, times FocusGuard AI turned the user around, focus time, tasks done, any running focus session and today's nudges.",
     S({}), RO, t_get_today),
    ("get_report", "Usage report over the last N days (1–30): total and per-site time on distracting sites, change vs the previous period, focus time, tasks done, per-day breakdown.",
     S({"days": {"type": "integer", "minimum": 1, "maximum": 30, "default": 7}}), RO, t_get_report),
    ("get_weekly_coaching", "The weekly coach: observations about the user's scrolling patterns and triggers, one suggestion, a matching technique from the knowledge base and a line from the user's own goals.",
     S({}), RO, t_get_weekly_coaching),
    ("get_plan", "Today's plan from the FocusGuard AI agent: first task, scheduled focus sessions and sites tightened for today.",
     S({}), RO, t_get_plan),
    ("plan_my_day", "Ask the FocusGuard AI agent to (re)plan today from the user's patterns, tasks and goals.", S({}), RW, t_plan_my_day),
    ("list_tasks", "The user's task list from the FocusGuard AI Focus page (the things they want to do instead of scrolling).",
     S({"include_done": {"type": "boolean", "default": False}}), RO, t_list_tasks),
    ("add_task", "Add a task to the user's Focus page.", S({"title": {"type": "string", "maxLength": 140}}, ["title"]), RW, t_add_task),
    ("complete_task", "Mark an open task as done (match by title or part of it).", S({"task": {"type": "string"}}, ["task"]), RW, t_complete_task),
    ("start_focus", "Start a focus session in the user's browser: opens the Focus page and blocks every distracting site until the timer ends. Optionally on a task (created if it doesn't exist).",
     S({"minutes": {"type": "integer", "minimum": 5, "maximum": 120, "default": 25}, "task": {"type": "string"}}), RW, t_start_focus),
    ("stop_focus", "Stop the running focus session.", S({}), RW, t_stop_focus),
    ("set_site_mode", "Change what happens on a site: off (ignored), track (counted, never blocked), limit (N minutes a day, then blocked) or block. Site can be a name like 'Instagram Reels' or a domain like netflix.com (added if new).",
     S({"site": {"type": "string"}, "mode": {"type": "string", "enum": ["off", "track", "limit", "block"]},
        "limit_minutes": {"type": "integer", "minimum": 1, "maximum": 600}}, ["site", "mode"]), RW, t_set_site_mode),
    ("break_down_task", "Break a task into small steps that each fit one 25-minute focus session (uses the local AI model when enabled). Optionally add them to the task list.",
     S({"task": {"type": "string"}, "add_to_list": {"type": "boolean", "default": False}}, ["task"]), RW, t_break_down_task),
    ("search_notes", "Search the user's FocusGuard AI knowledge base: their goals file, past weekly coaching, and the library of techniques for getting out of the scrolling loop.",
     S({"query": {"type": "string"}}, ["query"]), RO, t_search_notes),
]
BY_NAME = {name: fn for name, _, _, _, fn in TOOLS}


# ---------------------------------------------------------------------------
# MCP over stdio: newline-delimited JSON-RPC 2.0

def handle(msg: dict) -> dict | None:
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if mid is None:
        return None  # notification (e.g. notifications/initialized)
    if method == "initialize":
        asked = params.get("protocolVersion")
        return _ok(mid, {
            "protocolVersion": asked if asked in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": SERVER_INFO,
            "instructions": ("FocusGuard AI is the user's personal anti-doom-scrolling agent. Use these tools to see their "
                             "usage and tasks, coach them kindly and concretely, and act for them (start focus sessions, "
                             "add tasks, change site modes). Prefer specific numbers from the tools over guesses."),
        })
    if method == "ping":
        return _ok(mid, {})
    if method == "tools/list":
        return _ok(mid, {"tools": [{"name": n, "description": d, "inputSchema": s, "annotations": a}
                                   for n, d, s, a, _ in TOOLS]})
    if method == "tools/call":
        fn = BY_NAME.get(params.get("name"))
        if not fn:
            return _err(mid, -32602, f"Unknown tool: {params.get('name')}")
        try:
            text = fn(params.get("arguments") or {})
            return _ok(mid, {"content": [{"type": "text", "text": text}], "isError": False})
        except AppError as e:
            return _ok(mid, {"content": [{"type": "text", "text": str(e)}], "isError": True})
        except (TypeError, ValueError) as e:
            return _ok(mid, {"content": [{"type": "text", "text": f"Invalid arguments: {e}"}], "isError": True})
    if method in ("resources/list", "prompts/list"):
        return _ok(mid, {method.split("/")[0]: []})
    return _err(mid, -32601, f"Method not found: {method}")


def _ok(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def _err(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            out = _err(None, -32700, "Parse error")
        else:
            out = handle(msg) if isinstance(msg, dict) else _err(None, -32600, "Invalid request")
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
