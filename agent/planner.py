"""FocusGuard AI's AI agent: a language model that reads your data and acts
through tools to keep you off the feeds and on your own tasks.

Three jobs:
  plan       once a day: pick your first task, schedule focus sessions before
             your risky hours, tighten sites that got out of hand yesterday
  intervene  when you keep hitting blocked sites: decide on one concrete way out
  chat       in the dashboard (or via MCP): answer and take actions you ask for

The model (local, via Ollama) decides which tools to call. The tools enforce
the safety rules in code, whatever the model asks for:
  - sites can only get stricter (track → limit → block), and only until midnight
  - nothing starts without your click, except when you ask for it in chat
  - at most 3 focus sessions, 3 site changes and 3 new tasks per plan
Every decision is logged with its reason. Without a model, a rule-based
planner makes the same kind of plan, so the agent always works.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
import uuid

MODE_ORDER = {"off": 0, "track": 1, "limit": 2, "block": 3}
MAX_BLOCKS, MAX_CHANGES, MAX_NEW_TASKS = 3, 3, 3


def _fmt(ms: float) -> str:
    m = round((ms or 0) / 60000)
    return f"{m} min" if m < 60 else f"{m // 60} h {m % 60:02d} min"


def _hhmm_to_min(s: str) -> int | None:
    m = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(s or ""))
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    return h * 60 + mi if h < 24 and mi < 60 else None


def _min_to_hhmm(m: int) -> str:
    m %= 24 * 60
    return f"{m // 60:02d}:{m % 60:02d}"


def _ampm(hhmm: str) -> str:
    m = _hhmm_to_min(hhmm) or 0
    h, mi = divmod(m, 60)
    return f"{(h + 11) % 12 + 1}{':' + str(mi).zfill(2) if mi else ''} {'AM' if h < 12 else 'PM'}"


class Toolbox:
    """The agent's hands. Every write goes through here and is checked."""

    def __init__(self, engine, mode: str, now: float | None = None):
        self.e = engine
        self.mode = mode                      # plan | intervene | chat
        self.now = now or time.time()
        self.state = engine._get("ext_state", {}) or {}
        self.rules = {r["id"]: r for r in self.state.get("rules") or []}
        self.actions: list[str] = []          # human-readable log of what was done
        self.plan = {"focus_blocks": [], "site_changes": [], "first_task": None, "new_tasks": []}
        self.offer = None                     # intervene: the one-click way out
        self.finished_message = None

    # -- reading ---------------------------------------------------------------
    def _open_tasks(self) -> list[dict]:
        return [t for t in self.state.get("tasks") or [] if not t.get("done")]

    def _find_rule(self, site: str):
        q = str(site or "").strip().lower()
        for r in self.rules.values():
            if r["id"] == q or r["label"].lower() == q or q in [h.lower() for h in r.get("hosts", [])]:
                return r
        return None

    def get_overview(self) -> dict:
        days = self.state.get("days") or {}
        lt = time.localtime(self.now)
        today = time.strftime("%Y-%m-%d", lt)
        yday = time.strftime("%Y-%m-%d", time.localtime(self.now - 86400))
        label = lambda a: self.rules.get(a, {}).get("label", a)

        def per_site(day):
            return {label(a): round(s.get("ms", 0) / 60000) for a, s in ((day or {}).get("apps") or {}).items() if s.get("ms", 0) >= 60000}

        hours = [0.0] * 24
        week_keys = [time.strftime("%Y-%m-%d", time.localtime(self.now - i * 86400)) for i in range(1, 8)]
        for k in week_keys:
            for h, ms in enumerate(((days.get(k) or {}).get("hours") or [])[:24]):
                hours[h] += ms
        n_days = max(1, sum(1 for k in week_keys if days.get(k)))
        risky = [f"{h:02d}:00" for h in sorted(range(24), key=lambda h: -hours[h])[:3] if hours[h] / n_days >= 10 * 60000]
        prof = self.state.get("profile") or {}
        reasons: dict = {}
        for k in week_keys + [today]:
            for r, n in ((days.get(k) or {}).get("reasons") or {}).items():
                reasons[r] = reasons.get(r, 0) + n
        return {
            "now": time.strftime("%A %H:%M", lt),
            "today": {"distracting_minutes_by_site": per_site(days.get(today)),
                      "times_turned_around": sum(((days.get(today) or {}).get("blocked") or {}).values()),
                      "focus_minutes": round((days.get(today) or {}).get("focusMs", 0) / 60000)},
            "yesterday": {"distracting_minutes_by_site": per_site(days.get(yday)),
                          "focus_minutes": round((days.get(yday) or {}).get("focusMs", 0) / 60000)},
            "risky_hours_last_week": risky,
            "what_pulls_them_in": reasons or {t: "from setup" for t in prof.get("triggers") or []},
            "goal": prof.get("mainGoal"),
            "open_tasks": [{"title": t["title"], "focus_sessions_so_far": t.get("pomodoros", 0)} for t in self._open_tasks()[:10]],
            "sites": [{"site": r["label"], "mode": r["mode"], "daily_limit_min": r.get("limitMin")} for r in self.rules.values() if r["mode"] != "off"],
            "protected_hours": (prof.get("protect") or {}) if (prof.get("protect") or {}).get("enabled") else None,
            "focus_length_min": (self.state.get("focusSettings") or {}).get("workMin", 25),
        }

    def search_notes(self, query: str) -> list[dict]:
        return self.e.search_notes(str(query or "")[:200], k=3)

    # -- acting ----------------------------------------------------------------
    def _task_title(self, task: str | None) -> str | None:
        task = " ".join(str(task or "").split())[:140]
        if not task:
            return None
        for t in self._open_tasks():
            if t["title"].lower() == task.lower() or task.lower() in t["title"].lower():
                return t["title"]
        return self.add_task(task).get("title")

    def add_task(self, title: str) -> dict:
        title = " ".join(str(title or "").split())[:140]
        if not title:
            return {"error": "empty title"}
        if any(t["title"].lower() == title.lower() for t in self._open_tasks()) or title in self.plan["new_tasks"]:
            return {"ok": True, "title": title, "note": "already on the list"}
        if len(self.plan["new_tasks"]) >= MAX_NEW_TASKS:
            return {"error": f"at most {MAX_NEW_TASKS} new tasks at a time"}
        self.e.queue_command("add_task", {"title": title})
        self.plan["new_tasks"].append(title)
        self.state.setdefault("tasks", []).append({"id": "new", "title": title, "done": False})
        self.actions.append(f"Added the task “{title}”.")
        return {"ok": True, "title": title}

    def set_first_task(self, task: str) -> dict:
        title = self._task_title(task)
        if not title:
            return {"error": "give a task title"}
        self.plan["first_task"] = title
        return {"ok": True, "first_task": title}

    def schedule_focus(self, time_hhmm: str, minutes: int = 25, task: str | None = None) -> dict:
        start = _hhmm_to_min(time_hhmm)
        if start is None:
            return {"error": "time must be HH:MM, 24-hour"}
        lt = time.localtime(self.now)
        now_min = lt.tm_hour * 60 + lt.tm_min
        if start < now_min - 5:
            return {"error": f"{time_hhmm} has already passed today; it is {_min_to_hhmm(now_min)} now"}
        if len(self.plan["focus_blocks"]) >= MAX_BLOCKS:
            return {"error": f"at most {MAX_BLOCKS} focus sessions per plan"}
        try:
            minutes = max(10, min(90, int(minutes)))
        except (TypeError, ValueError):
            minutes = 25
        for b in self.plan["focus_blocks"]:
            if abs((_hhmm_to_min(b["time"]) or 0) - start) < 30:
                return {"error": "too close to another planned session; leave at least 30 minutes"}
        title = self._task_title(task) if task else None
        block = {"id": uuid.uuid4().hex[:8], "time": _min_to_hhmm(start), "minutes": minutes, "task": title, "status": "planned"}
        self.plan["focus_blocks"].append(block)
        self.actions.append(f"Planned {minutes} minutes of focus at {_ampm(block['time'])}" + (f" on “{title}”." if title else "."))
        return {"ok": True, **block}

    def tighten_site(self, site: str, mode: str, limit_minutes: int | None = None, reason: str = "") -> dict:
        r = self._find_rule(site)
        if not r:
            return {"error": f"unknown site {site!r}; use one from get_overview"}
        mode = str(mode or "").lower()
        if mode not in ("limit", "block"):
            return {"error": "mode must be limit or block"}
        cur, new = MODE_ORDER.get(r["mode"], 0), MODE_ORDER[mode]
        lim = None
        if mode == "limit":
            try:
                lim = max(5, min(120, int(limit_minutes or 20)))
            except (TypeError, ValueError):
                lim = 20
        stricter = new > cur or (mode == "limit" and r["mode"] == "limit" and lim < (r.get("limitMin") or 999))
        if not stricter:
            return {"error": f"{r['label']} is already {r['mode']}; the agent may only make sites stricter"}
        if r["mode"] == "off":
            return {"error": f"{r['label']} is turned off by the user; leave it alone"}
        if len(self.plan["site_changes"]) >= MAX_CHANGES:
            return {"error": f"at most {MAX_CHANGES} site changes per plan"}
        change = {"site": r["id"], "label": r["label"], "from": r["mode"], "from_limit": r.get("limitMin"),
                  "to": mode, "limit_minutes": lim, "reason": str(reason)[:200]}
        self.plan["site_changes"].append(change)
        self.e.queue_command("set_site_mode", {"site": r["id"], "mode": mode, **({"limit_minutes": lim} if lim else {})})
        self.e.remember_temp_change(change)
        r["mode"], r["limitMin"] = mode, lim or r.get("limitMin")
        what = f"limited {r['label']} to {lim} minutes" if mode == "limit" else f"blocked {r['label']}"
        self.actions.append(f"For today, {what}" + (f" ({reason})." if reason else "."))
        return {"ok": True, **change, "until": "midnight"}

    def start_focus_now(self, minutes: int = 25, task: str | None = None) -> dict:
        if self.mode != "chat":
            return {"error": "only allowed when the user asks for it in chat"}
        try:
            minutes = max(5, min(120, int(minutes)))
        except (TypeError, ValueError):
            minutes = 25
        title = self._task_title(task) if task else None
        self.e.queue_command("start_focus", {"minutes": minutes, "task": title})
        self.actions.append(f"Started a {minutes}-minute focus session" + (f" on “{title}”." if title else "."))
        return {"ok": True, "minutes": minutes, "task": title}

    def offer_focus(self, task: str | None = None, minutes: int = 10, message: str = "") -> dict:
        if self.mode != "intervene":
            return {"error": "only used when stepping in"}
        title = self._task_title(task) if task else (self._open_tasks()[0]["title"] if self._open_tasks() else None)
        try:
            minutes = max(5, min(45, int(minutes)))
        except (TypeError, ValueError):
            minutes = 10
        self.offer = {"action": "focus", "task": title, "minutes": minutes, "message": str(message)[:300]}
        return {"ok": True, **self.offer}

    def finish(self, message: str) -> dict:
        self.finished_message = str(message or "").strip()[:600]
        return {"ok": True}


TOOL_SPECS = {
    "get_overview": ("Everything you need to know about the user right now: today, yesterday, risky hours, triggers, goal, open tasks, site rules.", {}),
    "search_notes": ("Search the user's goals, past coaching and the techniques library.", {"query": {"type": "string"}}),
    "add_task": ("Add a task to the user's list.", {"title": {"type": "string"}}),
    "set_first_task": ("Choose the one task the user should start with today.", {"task": {"type": "string"}}),
    "schedule_focus": ("Plan a focus session later today. The user gets a one-click prompt at that time.",
                       {"time_hhmm": {"type": "string", "description": "24-hour HH:MM, later today"},
                        "minutes": {"type": "integer"}, "task": {"type": "string"}}),
    "tighten_site": ("Make a site stricter for the rest of today (limit or block). Can never loosen a site.",
                     {"site": {"type": "string"}, "mode": {"type": "string", "enum": ["limit", "block"]},
                      "limit_minutes": {"type": "integer"}, "reason": {"type": "string"}}),
    "start_focus_now": ("Start a focus session right now (only when the user asks in chat).",
                        {"minutes": {"type": "integer"}, "task": {"type": "string"}}),
    "offer_focus": ("Offer the user one concrete way out of a scrolling loop: a short focus session on a task.",
                    {"task": {"type": "string"}, "minutes": {"type": "integer"}, "message": {"type": "string"}}),
    "finish": ("End your turn with a short, warm message to the user (2-3 sentences, mention the plan).", {"message": {"type": "string"}}),
}
MODE_TOOLS = {
    "plan": ["get_overview", "search_notes", "set_first_task", "schedule_focus", "tighten_site", "add_task", "finish"],
    "intervene": ["get_overview", "offer_focus", "tighten_site", "finish"],
    "chat": ["get_overview", "search_notes", "add_task", "schedule_focus", "tighten_site", "start_focus_now"],
}

SYSTEM = (
    "You are FocusGuard AI, a personal AI agent that helps one person stop doom-scrolling and spend their time on what "
    "matters to them. You act through tools. Always call get_overview first. Be concrete and kind, never preachy. "
    "Use the person's own goal and tasks. Base decisions on the numbers you see; never invent numbers. "
    "Sites can only be made stricter and only for today. Prefer one clear next step over many."
)
MODE_PROMPTS = {
    "plan": ("Make today's plan. Choose the first task (set_first_task). Schedule 1 to 3 focus sessions later today "
             "(schedule_focus), at least one starting shortly before their risky hours. If a site took more than 45 minutes "
             "yesterday and is only tracked, consider tighten_site with a reason. Then call finish with a 2-3 sentence message."),
    "intervene": ("The person keeps trying to open blocked sites right now: {detail}. Decide on one concrete way out: call "
                  "offer_focus with a task and a short session (10-15 minutes) and a one-sentence message in their terms. "
                  "Optionally tighten one site for today. Then call finish."),
}


class Agent:
    def __init__(self, engine):
        self.e = engine

    # -- model loop ------------------------------------------------------------
    def _chat(self, messages: list[dict], tools: list[str]) -> dict:
        openai_cfg = self.e.cfg.get("openai") or {}
        if openai_cfg.get("enabled") and __import__("os").environ.get("OPENAI_API_KEY"):
            try:
                from openai import OpenAI
                client = OpenAI(timeout=openai_cfg.get("timeout_s", 30))
                openai_messages = []
                for m in messages:
                    item = {"role": m.get("role"), "content": m.get("content", "")}
                    if m.get("role") == "assistant" and m.get("tool_calls"):
                        item["tool_calls"] = [{"id": c.get("id", uuid.uuid4().hex), "type": "function",
                                               "function": c.get("function", {})} for c in m["tool_calls"]]
                    if m.get("role") == "tool":
                        item["tool_call_id"] = m.get("tool_call_id", "")
                    openai_messages.append(item)
                result = client.chat.completions.create(
                    model=openai_cfg.get("model", "gpt-4.1-mini"),
                    messages=openai_messages,
                    tools=[{"type": "function", "function": {
                        "name": n, "description": TOOL_SPECS[n][0],
                        "parameters": {"type": "object", "properties": TOOL_SPECS[n][1]}}} for n in tools],
                    tool_choice="auto",
                )
                msg = result.choices[0].message
                return {"content": msg.content or "", "tool_calls": [
                    {"function": {"name": c.function.name, "arguments": c.function.arguments}, "id": c.id}
                    for c in (msg.tool_calls or [])
                ]}
            except Exception:
                # The caller falls back to Ollama/rules; a provider outage must
                # never stop blocking or intervention safety behavior.
                pass
        o = self.e.cfg["ollama"]
        body = {"model": o["model"], "messages": messages, "stream": False,
                "tools": [{"type": "function", "function": {
                    "name": n, "description": TOOL_SPECS[n][0],
                    "parameters": {"type": "object", "properties": TOOL_SPECS[n][1]}}} for n in tools]}
        req = urllib.request.Request(o["url"].rstrip("/") + "/api/chat", data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=max(o.get("timeout_s", 4), 60)) as r:
            return json.loads(r.read()).get("message") or {}

    def _run_model(self, tb: Toolbox, tools: list[str], user_msg: str, history: list[dict] | None = None,
                   max_steps: int = 8) -> str | None:
        messages = [{"role": "system", "content": SYSTEM}] + (history or []) + [{"role": "user", "content": user_msg}]
        for _ in range(max_steps):
            msg = self._chat(messages, tools)
            calls = msg.get("tool_calls") or []
            messages.append({"role": "assistant", "content": msg.get("content", ""), "tool_calls": calls})
            if not calls:
                return msg.get("content", "").strip() or tb.finished_message
            for c in calls:
                fn = (c.get("function") or {})
                name, args = fn.get("name"), fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                if name not in tools or not hasattr(tb, name):
                    result = {"error": f"unknown tool {name}"}
                else:
                    try:
                        result = getattr(tb, name)(**{k: v for k, v in args.items() if k in TOOL_SPECS[name][1]})
                    except TypeError as e:
                        result = {"error": f"bad arguments: {e}"}
                tool_message = {"role": "tool", "content": json.dumps(result)[:4000], "tool_name": name}
                if c.get("id"):
                    tool_message["tool_call_id"] = c["id"]
                messages.append(tool_message)
            if tb.finished_message is not None:
                return tb.finished_message
        return tb.finished_message

    def _model_on(self) -> bool:
        o = self.e.cfg["ollama"]
        p = self.e.cfg.get("openai") or {}
        return bool(o.get("enabled") or (p.get("enabled") and __import__("os").environ.get("OPENAI_API_KEY")))

    def _model_source(self) -> str:
        p = self.e.cfg.get("openai") or {}
        return "openai" if p.get("enabled") and __import__("os").environ.get("OPENAI_API_KEY") else "ollama"

    # -- plan --------------------------------------------------------------------
    def make_plan(self, now: float | None = None, reason: str = "daily") -> dict:
        tb = Toolbox(self.e, "plan", now)
        source, message = "rules", None
        if self._model_on():
            try:
                message = self._run_model(tb, MODE_TOOLS["plan"], MODE_PROMPTS["plan"])
                source = self._model_source()
            except Exception:
                tb = Toolbox(self.e, "plan", now)  # start clean; nothing half-done from a failed model run
                source = "rules"
        if source == "rules":
            message = self._rule_plan(tb)
        if not tb.plan["first_task"] and tb._open_tasks():
            tb.plan["first_task"] = tb._open_tasks()[0]["title"]
        plan = {"date": time.strftime("%Y-%m-%d", time.localtime(tb.now)), "made_at": int(tb.now * 1000),
                "source": source, "reason": reason, "message": message or "", **tb.plan, "actions": tb.actions}
        self.e.save_plan(plan)
        self.e.log_agent("plan", plan["message"], tb.actions, source)
        return plan

    def _rule_plan(self, tb: Toolbox) -> str:
        ov = tb.get_overview()
        lt = time.localtime(tb.now)
        now_min = lt.tm_hour * 60 + lt.tm_min
        tasks = tb._open_tasks()
        first = tasks[0]["title"] if tasks else None
        if first:
            tb.set_first_task(first)
        mins = ov["focus_length_min"]
        risky = [_hhmm_to_min(h) for h in ov["risky_hours_last_week"]]
        risky = [r for r in risky if r is not None]
        # One session soon, one just before your riskiest hour.
        soon = ((now_min + 15) // 30 + 1) * 30
        if soon < 23 * 60:
            tb.schedule_focus(_min_to_hhmm(soon), mins, first)
        if risky:
            before = min(risky) - 30
            second = tasks[1]["title"] if len(tasks) > 1 else first
            if before > now_min:
                tb.schedule_focus(_min_to_hhmm(before), mins, second)
        # Tighten tracked sites that ran over 45 minutes yesterday.
        for site, m in sorted(ov["yesterday"]["distracting_minutes_by_site"].items(), key=lambda x: -x[1]):
            r = tb._find_rule(site)
            if r and r["mode"] == "track" and m >= 45:
                tb.tighten_site(r["id"], "limit", 20, f"{m} minutes yesterday")
        parts = []
        y = ov["yesterday"]["distracting_minutes_by_site"]
        if y:
            top = max(y.items(), key=lambda x: x[1])
            parts.append(f"Yesterday: {_fmt(sum(y.values()) * 60000)} on distracting sites, most of it on {top[0]}.")
        if first:
            parts.append(f"Start with “{first}”.")
        blocks = tb.plan["focus_blocks"]
        if blocks:
            parts.append("I planned focus at " + " and ".join(_ampm(b["time"]) for b in blocks) +
                         (", just before the time you usually drift." if risky and len(blocks) > 1 else "."))
        for c in tb.plan["site_changes"]:
            parts.append(f"{c['label']} is limited to {c['limit_minutes']} minutes today.")
        return " ".join(parts) or "No data yet. Add a task and I'll plan around it."

    # -- intervene --------------------------------------------------------------
    def intervene(self, detail: str, now: float | None = None) -> dict:
        tb = Toolbox(self.e, "intervene", now)
        source, message = "rules", None
        if self._model_on():
            try:
                message = self._run_model(tb, MODE_TOOLS["intervene"], MODE_PROMPTS["intervene"].format(detail=detail), max_steps=6)
                source = self._model_source()
            except Exception:
                tb = Toolbox(self.e, "intervene", now)
        if not tb.offer:
            source = "rules" if source != "ollama" else source
            t = tb._open_tasks()
            task = t[0]["title"] if t else None
            msg = (f"You've reached for blocked sites {detail}. Let's break it: 10 minutes on “{task}”, everything else stays closed."
                   if task else f"You've reached for blocked sites {detail}. Let's break it with a 10-minute reset away from the screen.")
            tb.offer_focus(task, 10, msg) if task else None
            tb.offer = tb.offer or {"action": "reset", "task": None, "minutes": 10, "message": msg}
        offer = tb.offer
        offer["message"] = offer.get("message") or message or "Let's step out of this loop."
        self.e.log_agent("intervene", offer["message"], tb.actions, source)
        return {"offer": offer, "actions": tb.actions, "source": source}

    # -- chat -------------------------------------------------------------------
    def chat(self, text: str, history: list[dict] | None = None) -> dict:
        tb = Toolbox(self.e, "chat")
        text = " ".join(str(text or "").split())[:800]
        if self._model_on():
            try:
                hist = [{"role": m["role"], "content": str(m["content"])[:800]} for m in (history or [])[-6:]
                        if m.get("role") in ("user", "assistant")]
                answer = self._run_model(tb, MODE_TOOLS["chat"], text, hist)
                source = self._model_source()
                self.e.log_agent("chat", text, tb.actions, source)
                return {"answer": answer or "Done.", "actions": tb.actions, "source": source}
            except Exception:
                tb = Toolbox(self.e, "chat")
        # Without a model: understand a few clear requests, answer the rest from data and notes.
        low = text.lower()
        if re.search(r"\bplan\b", low):
            plan = self.make_plan(reason="asked")
            return {"answer": plan["message"], "actions": plan["actions"], "source": "rules", "plan": plan}
        if "focus" in low and any(w in low for w in ("start", "begin", "now", "help me focus", "can't focus", "cant focus")):
            mm = re.search(r"(\d{1,3})\s*-?\s*min", low)
            minutes = int(mm.group(1)) if mm else ((self.e._get("ext_state", {}) or {}).get("focusSettings") or {}).get("workMin", 25)
            mt = re.search(r"focus(?:ing)? on (.+)$", text, re.I)
            task = mt.group(1).strip(" .!?") if mt else (tb._open_tasks()[0]["title"] if tb._open_tasks() else None)
            tb.start_focus_now(minutes, task)
            self.e.log_agent("chat", text, tb.actions, "rules")
            return {"answer": " ".join(tb.actions) + " Your browser switches over within a few seconds.",
                    "actions": tb.actions, "source": "rules"}
        r = self.e.ask(text)
        return {"answer": r["answer"], "sources": r.get("sources", []), "actions": [], "source": r.get("source", "rules")}
