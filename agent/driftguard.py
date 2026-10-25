"""FocusGuard AI — the always-on agent.

One background process that:
  Detect    receives real browser signals (collector.py)
  Understand runs the deterministic rules every few seconds (detector.py)
  Redirect  sends an in-page card to the extension; falls back to a desktop notification
  Protect   enforces Focus Rescue (the extension blocks distracting sites until it ends)
  Recover   records what happened after each nudge: left, stayed, rescued, finished

Start it once and forget it:
    python3 agent/driftguard.py              # tray icon if pystray is installed
    python3 agent/autostart.py install       # start at every login

Status page: http://127.0.0.1:8765/
"""

from __future__ import annotations

import datetime
import html
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import webbrowser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import coach  # noqa: E402
import collector  # noqa: E402
import detector  # noqa: E402
import planner  # noqa: E402
import rag  # noqa: E402
from notify import notify  # noqa: E402

CONFIG_PATH = Path(os.environ.get("DRIFTGUARD_CONFIG", collector.ROOT / "data" / "config.json"))

CONFIG_DEFAULTS = {
    "rules": detector.DEFAULTS,
    "rescue_minutes": 10,
    "cooldown_minutes": 20,          # same kind of nudge for the same app at most this often
    "snooze_minutes": 5,             # "give me 5 more minutes"
    "after_rescue_quiet_minutes": 30,
    "native_fallback_seconds": 20,   # desktop notification if the in-page card wasn't picked up
    "outcome_after_minutes": 3,      # when to judge whether you left after a nudge
    "tick_seconds": 10,
    "ollama": {"enabled": False, "url": "http://127.0.0.1:11434", "model": "llama3.2",
               "embed_model": "nomic-embed-text", "timeout_s": 4},
    "openai": {"enabled": False, "model": "gpt-4.1-mini", "timeout_s": 30},
    "knowledge_paths": [],           # extra folders of Markdown for the coach / MCP search, e.g. ["docs"]
    "rag_embeddings": False,         # re-rank search with local embeddings (needs Ollama)
    "baseline": {"enabled": True, "factor": 2.0, "floor_minutes": 20, "window_hours": 3},
    "agent": {"enabled": True, "daily_plan": True, "struggle_blocks": 3, "struggle_window_minutes": 20, "intervene_cooldown_minutes": 30,
              "max_autonomous_interventions_per_day": 6},
}

COMMAND_TYPES = {"add_task", "complete_task", "start_focus", "stop_focus", "set_site_mode", "pause"}
SITE_MODES = {"off", "track", "limit", "block"}

ENGINE_SCHEMA = """
CREATE TABLE IF NOT EXISTS alerts (
    id           TEXT PRIMARY KEY,
    created_at   INTEGER NOT NULL,
    kind         TEXT NOT NULL,
    app          TEXT NOT NULL,
    session_id   TEXT,
    headline     TEXT NOT NULL,
    explanation  TEXT NOT NULL,
    stats        TEXT,
    level        INTEGER NOT NULL DEFAULT 1,
    delivered    TEXT,          -- page | native
    delivered_at INTEGER,
    outcome      TEXT,          -- left | stayed | snoozed | dismissed | rescued | rescue_completed | rescue_ended_early
    outcome_at   INTEGER
);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at);
CREATE TABLE IF NOT EXISTS control (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS agent_log (
    id      INTEGER PRIMARY KEY AUTOINCREMENT,
    ts      INTEGER NOT NULL,
    kind    TEXT NOT NULL,      -- plan | intervene | chat | prompt | revert
    message TEXT,
    actions TEXT,               -- JSON list of what the agent did
    source  TEXT                -- ollama | rules
);
"""


def load_config() -> dict:
    cfg = json.loads(json.dumps(CONFIG_DEFAULTS))
    if CONFIG_PATH.exists():
        try:
            user = json.loads(CONFIG_PATH.read_text())
            cfg.update({k: v for k, v in user.items() if k not in ("rules", "ollama", "baseline", "agent")})
            cfg["agent"].update(user.get("agent", {}))
            cfg["rules"].update(user.get("rules", {}))
            cfg["ollama"].update(user.get("ollama", {}))
            cfg["openai"].update(user.get("openai", {}))
            cfg["baseline"].update(user.get("baseline", {}))
        except (ValueError, OSError) as e:
            print(f"config.json ignored ({e})", file=sys.stderr)
    else:
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(CONFIG_DEFAULTS, indent=2))
    return cfg


def now_ms() -> int:
    return int(time.time() * 1000)


class Engine(collector.Hooks):
    def __init__(self, conn, cfg: dict | None = None, token: str = ""):
        self.conn = conn
        self.cfg = cfg or load_config()
        self.token = token
        self.lock = collector.DB_LOCK
        with self.lock:
            conn.executescript(ENGINE_SCHEMA)
        self.listeners = []          # tray subscribes to state changes
        self._stop = threading.Event()
        self.history_dir = CONFIG_PATH.parent / "history"
        self.profile_dir = CONFIG_PATH.parent / "profile"
        self.kb = rag.Knowledge(collector.ROOT, self.cfg.get("knowledge_paths"), self.history_dir,
                                self.cfg["ollama"], self.cfg.get("rag_embeddings", False), self.profile_dir)
        self.agent = planner.Agent(self)
        self._agent_busy = threading.Lock()
        self._set("runtime", {"started_at": now_ms(), "last_heartbeat": now_ms(),
                               "last_tick": 0, "consecutive_errors": 0, "last_error": None,
                               "pid": os.getpid(), "version": "0.14-production"})

    def _runtime_update(self, **updates):
        state = self._get("runtime", {}) or {}
        state.update(updates)
        self._set("runtime", state)

    def runtime_status(self) -> dict:
        state = self._get("runtime", {}) or {}
        age_ms = max(0, now_ms() - int(state.get("last_heartbeat") or 0))
        return {**state, "healthy": age_ms < max(60000, self.cfg.get("tick_seconds", 10) * 6000),
                "heartbeat_age_ms": age_ms, "autonomy_enabled": bool(self.cfg.get("agent", {}).get("enabled", True))}

    # -- control state (persisted, so a restart keeps a running rescue) -------
    def _get(self, key, default=0):
        with self.lock:
            row = self.conn.execute("SELECT value FROM control WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key, value):
        with self.lock, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO control(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    def control(self) -> dict:
        t = now_ms()
        paused = self._get("paused_until")
        rescue = self._get("rescue_until")
        return {
            "paused_until": paused if paused > t else 0,
            "rescue_until": rescue if rescue > t else 0,
            "rescue_ref": self._get("rescue_ref", None) if rescue > t else None,
        }

    def _changed(self):
        for fn in self.listeners:
            try:
                fn()
            except Exception:
                pass

    # -- actions (from the extension, the tray, or the status page) -----------
    def start_rescue(self, minutes: float | None = None, ref: str | None = None, start_ms: int | None = None):
        minutes = minutes or self.cfg["rescue_minutes"]
        start = start_ms or now_ms()
        self._set("rescue_until", int(start + minutes * 60000))
        self._set("rescue_ref", ref)
        if ref:
            self._outcome(ref, "rescued", overwrite=True)
        self._changed()

    def end_rescue(self, reason: str = "completed"):
        ref = self._get("rescue_ref", None)
        was_active = self._get("rescue_until") > now_ms() - 5000
        self._set("rescue_until", 0)
        self._set("rescue_ref", None)
        self._set("quiet_until", now_ms() + self.cfg["after_rescue_quiet_minutes"] * 60000)
        if ref:
            self._outcome(ref, "rescue_completed" if reason.startswith("completed") else "rescue_ended_early", overwrite=True)
        if was_active and reason.startswith("completed"):
            notify("Focus Rescue finished", "You made it out of the loop. Nice.")
        self._changed()

    def pause(self, minutes: float):
        self._set("paused_until", int(now_ms() + minutes * 60000) if minutes > 0 else 0)
        self._changed()

    # -- alerts ---------------------------------------------------------------
    def _outcome(self, alert_id: str, outcome: str, overwrite: bool = False):
        with self.lock, self.conn:
            q = "UPDATE alerts SET outcome=?, outcome_at=? WHERE id=?"
            if not overwrite:
                q += " AND outcome IS NULL"
            self.conn.execute(q, (outcome, now_ms(), alert_id))

    def _recent_alert(self, kind: str, app: str, since: int):
        with self.lock:
            return self.conn.execute(
                "SELECT * FROM alerts WHERE kind=? AND app=? AND created_at>=? ORDER BY created_at DESC LIMIT 1",
                (kind, app, since)).fetchone()

    def _coach_rewrite(self, alert: detector.Alert) -> str:
        """Optional: let a local Ollama model personalise the wording. The rule
        already fired; the model only rephrases, and any failure keeps the template."""
        o = self.cfg["ollama"]
        if not o.get("enabled"):
            return alert.explanation
        prompt = (
            "You are FocusGuard AI, a calm, non-judgmental coach that helps someone exit a scrolling loop. "
            "Rewrite this observation in at most two short sentences, second person, no lecturing, "
            "no emojis, keep the numbers:\n\n"
            f"{alert.headline}. {alert.explanation}"
        )
        try:
            req = urllib.request.Request(
                o["url"].rstrip("/") + "/api/generate",
                data=json.dumps({"model": o["model"], "prompt": prompt, "stream": False}).encode(),
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=o.get("timeout_s", 4)) as r:
                text = json.loads(r.read()).get("response", "").strip()
            return text if 20 < len(text) < 400 else alert.explanation
        except Exception:
            return alert.explanation

    def tick(self):
        t = now_ms()
        self._runtime_update(last_tick=t, last_heartbeat=t, consecutive_errors=0, last_error=None)
        ctl = self.control()
        self._resolve_outcomes(t)
        self._native_fallback(t)
        self._agent_duties(t, ctl)
        if ctl["paused_until"] or ctl["rescue_until"] or self._get("quiet_until") > t or self._get("focus_until") > t:
            return
        sessions = collector.build_sessions(self.conn, since_ms=t - 2 * 3600 * 1000, now_ms=t)
        alerts = detector.detect(sessions, t, self.cfg["rules"])
        # Goal-aware intervention: page type is coarse by design, but enough to
        # notice a mismatch without reading URLs, titles, or page text.
        current = next((s for s in reversed(sessions) if s["status"] == "open"), None)
        profile = (self._get("ext_state", {}) or {}).get("profile") or {}
        alignment = detector.intent_mismatch(profile, current)
        if current and alignment["mismatch"]:
            score = detector.drift_score(current, {"intent_mismatch": True})
            if score["score"] >= 60:
                alerts.append(detector.Alert(
                    kind="intent_mismatch", app=current["app"], session_id=current["session_id"],
                    headline="This may not be what you meant to open",
                    explanation=alignment["reason"] + " Pick a next action before the feed picks one for you.",
                    stats={"drift_score": score["score"], "drift_label": score["label"],
                           "intent_mismatch": True},
                ))
        b = self._baseline(sessions, t)
        if b:
            alerts.append(b)
        for a in alerts:
            prev = self._recent_alert(a.kind, a.app, t - self.cfg["cooldown_minutes"] * 60000)
            level = 1
            if prev:
                snoozed_ok = prev["outcome"] == "snoozed" and t - (prev["outcome_at"] or t) >= self.cfg["snooze_minutes"] * 60000
                if not snoozed_ok:
                    continue  # still cooling down
                level = 2     # second nudge after a snooze is firmer
            explanation = self._coach_rewrite(a)
            if level == 2:
                explanation += " You asked for five more minutes — they're up."
            with self.lock, self.conn:
                self.conn.execute(
                    """INSERT INTO alerts (id, created_at, kind, app, session_id, headline, explanation, stats, level)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (str(uuid.uuid4()), t, a.kind, a.app, a.session_id, a.headline, explanation,
                     json.dumps(a.stats), level))
            self._changed()
            break  # one nudge at a time is plenty

    def _native_fallback(self, t):
        cutoff = t - self.cfg["native_fallback_seconds"] * 1000
        with self.lock:
            rows = self.conn.execute(
                "SELECT * FROM alerts WHERE delivered IS NULL AND created_at <= ? AND created_at >= ?",
                (cutoff, t - 10 * 60000)).fetchall()
        for r in rows:
            notify(f"FocusGuard AI · {r['headline']}", r["explanation"])
            with self.lock, self.conn:
                self.conn.execute("UPDATE alerts SET delivered='native', delivered_at=? WHERE id=?", (t, r["id"]))

    def _adaptive_plan(self, kind: str, app: str) -> dict:
        """Learn from this user's recent outcomes for this kind of nudge."""
        with self.lock:
            rows = self.conn.execute(
                "SELECT outcome FROM alerts WHERE kind=? AND app=? AND outcome IS NOT NULL "
                "ORDER BY created_at DESC LIMIT 12", (kind, app)).fetchall()
        return detector.adaptive_intervention([r["outcome"] for r in rows], self.cfg["rescue_minutes"])

    def _resolve_outcomes(self, t):
        """Recover: a few minutes after a nudge with no explicit answer, did you leave?"""
        cutoff = t - self.cfg["outcome_after_minutes"] * 60000
        with self.lock:
            rows = self.conn.execute(
                "SELECT id, app FROM alerts WHERE outcome IS NULL AND delivered IS NOT NULL AND created_at <= ?",
                (cutoff,)).fetchall()
        if not rows:
            return
        open_apps = {s["app"] for s in collector.build_sessions(self.conn, since_ms=t - 3600000, now_ms=t)
                     if s["status"] == "open"}
        for r in rows:
            self._outcome(r["id"], "stayed" if r["app"] in open_apps else "left")

    # -- Hooks used by the HTTP server ---------------------------------------
    def after_ingest(self, events: list[dict]) -> dict:
        for e in events:
            if e["type"] == "nudge_response" and e["ref"]:
                outcome = {"snooze": "snoozed", "dismiss": "dismissed", "rescue": "rescued", "accept": "accepted"}.get(e["reason"] or "")
                if outcome:
                    self._outcome(e["ref"], outcome, overwrite=True)
                    self._plan_block_from_alert(e["ref"], "started" if outcome == "accepted" else "skipped")
            elif e["type"] == "focus_rescue_start":
                self.start_rescue((e["duration_ms"] or 0) / 60000 or None, e["ref"], e["ts"])
            elif e["type"] == "focus_rescue_end":
                self.end_rescue(e["reason"] or "completed")
            elif e["type"] == "pause":
                self.pause((e["duration_ms"] or 0) / 60000)
            elif e["type"] == "focus_start":
                self._set("focus_until", e["ts"] + (e["duration_ms"] or 25 * 60000))
            elif e["type"] == "focus_end":
                self._set("focus_until", 0)
        if any(e["type"] in ("session_start", "short_viewed", "scroll_burst") for e in events):
            self.tick()  # react right away instead of waiting for the next tick
        if any(e["type"] == "blocked" for e in events):
            self._check_struggle()
        return {"nudges": self._take_nudges(), "control": self.control(), "commands": self._take_commands(),
                "plan": self.plan_summary()}

    def _take_nudges(self) -> list[dict]:
        t = now_ms()
        with self.lock, self.conn:
            rows = self.conn.execute(
                "SELECT * FROM alerts WHERE delivered IS NULL AND created_at >= ? ORDER BY created_at",
                (t - 5 * 60000,)).fetchall()
            for r in rows:
                self.conn.execute("UPDATE alerts SET delivered='page', delivered_at=? WHERE id=?", (t, r["id"]))
        out = []
        for r in rows:
            extra = {}
            adaptive = self._adaptive_plan(r["kind"], r["app"])
            try:
                st_ = json.loads(r["stats"] or "{}")
                extra = {k: st_[k] for k in ("action", "task", "minutes", "cta", "drift_score", "drift_label", "intent_mismatch") if k in st_}
            except ValueError:
                pass
            out.append({"id": r["id"], "kind": r["kind"], "app": r["app"], "headline": r["headline"],
                        "explanation": r["explanation"], "level": r["level"],
                        "rescue_minutes": adaptive["minutes"], "snooze_minutes": self.cfg["snooze_minutes"],
                        "adaptive_reason": adaptive["reason"], **extra})
        return out

    def handle_get(self, path, query):
        if path == "/api/runtime":
            return 200, self.runtime_status()
        if path == "/api/status":
            return 200, self.status()
        if path == "/api/alerts":
            with self.lock:
                rows = self.conn.execute("SELECT * FROM alerts ORDER BY created_at DESC LIMIT 50").fetchall()
            return 200, {"alerts": [dict(r) for r in rows]}
        if path == "/api/control":
            return 200, {**self.control(), "commands": self._take_commands(), "plan": self.plan_summary(),
                         "nudges": self._take_nudges()}
        if path == "/api/plan":
            return 200, self._get("plan", {}) or {}
        if path == "/api/agent/log":
            with self.lock:
                rows = self.conn.execute("SELECT * FROM agent_log ORDER BY ts DESC LIMIT 40").fetchall()
            return 200, {"log": [{**dict(r), "actions": json.loads(r["actions"] or "[]")} for r in rows]}
        if path == "/api/state":
            return 200, self._get("ext_state", {}) or {}
        if path == "/api/notes":
            q = (query.get("q") or [""])[0]
            return 200, {"results": self.search_notes(q)}
        return None

    def coach(self, payload: dict) -> dict:
        ext = self._get("ext_state", {}) or {}
        days = payload.get("days") if isinstance(payload.get("days"), dict) and payload.get("days") else (ext.get("days") or {})
        rules = ext.get("rules") or []
        labels = payload.get("labels") if isinstance(payload.get("labels"), dict) and payload.get("labels") else {r["id"]: r["label"] for r in rules}
        modes = payload.get("modes") if isinstance(payload.get("modes"), dict) and payload.get("modes") else {r["id"]: r["mode"] for r in rules}
        week_ago = now_ms() - 7 * 86400000
        with self.lock:
            rows = self.conn.execute("SELECT outcome FROM alerts WHERE created_at >= ?", (week_ago,)).fetchall()
        exits = sum(1 for r in rows if r["outcome"] in ("left", "rescued", "rescue_completed", "dismissed"))
        triggers = ((ext.get("profile") or {}).get("triggers")) or None
        result = coach.weekly_insights(days, labels, {"total": len(rows), "exits": exits}, modes, triggers=triggers)
        result.update(self._ground(result.get("themes") or []))
        context = {k: result[k] for k in ("technique", "goal") if result.get(k)}
        result = coach.rewrite_with_llm(result, self.cfg["ollama"], context)
        self._save_history(result)
        return result

    # -- RAG -------------------------------------------------------------------
    def _ground(self, themes: list[str]) -> dict:
        """Retrieve one matching technique and, if you've written any, a line from your goals."""
        out = {}
        query = " ".join(themes)
        tech = self.kb.search(query, k=1, kinds=["techniques"])
        if tech:
            t = tech[0]
            secs = t["sections"]
            out["technique"] = {
                "title": t["title"],
                "how": rag.first_sentences(secs.get("How to do it", t["text"]), 2),
                "try": rag.first_sentences(secs.get("Try this in FocusGuard AI", ""), 2),
                "source": t["path"],
            }
        goal = self.kb.search(query + " goal matters instead weak moment why", k=1, kinds=["me"])
        if goal:
            g = goal[0]
            out["goal"] = {"section": g["section"], "text": rag.first_sentences(g["text"], 2, 220), "source": g["path"]}
        return out

    def _save_history(self, result: dict):
        """Keep each week's coaching as Markdown so future coaching (and you) can look back."""
        try:
            self.history_dir.mkdir(parents=True, exist_ok=True)
            year, week, _ = datetime.date.today().isocalendar()
            lines = [f"# Week {week}, {year}", "", "## Summary", result["headline"], "",
                     "## Observations", *[f"- {i}" for i in result.get("insights", [])], "",
                     "## Suggestion", result.get("suggestion", "")]
            if result.get("technique"):
                lines += ["", "## Technique", f"{result['technique']['title']}: {result['technique']['how']}"]
            (self.history_dir / f"{year}-W{week:02d}.md").write_text("\n".join(lines) + "\n")
        except OSError:
            pass

    def search_notes(self, query: str, k: int = 5) -> list[dict]:
        hits = self.kb.search(query, k=k)
        return [{"title": h["title"], "section": h["section"], "source": h["path"], "kind": h["kind"],
                 "excerpt": rag.first_sentences(h["text"], 3, 400)} for h in hits]

    # -- coach chat: answer a question from your data + notes (RAG) ---------------
    def ask(self, question: str) -> dict:
        q = " ".join(str(question or "").split())[:500]
        if not q:
            return {"answer": "Ask me anything about your scrolling, your week or your goals.", "sources": [], "source": "rules"}
        ext = self._get("ext_state", {}) or {}
        days = ext.get("days") or {}
        rules = ext.get("rules") or []
        labels = {r["id"]: r["label"] for r in rules}
        week = coach.weekly_insights(days, labels, None, {r["id"]: r["mode"] for r in rules})
        today = (days.get(time.strftime("%Y-%m-%d")) or {})
        t_ms = sum((a or {}).get("ms", 0) for a in (today.get("apps") or {}).values())
        today_line = (f"Today so far: {coach._fmt(t_ms)} on distracting sites, focused {coach._fmt(today.get('focusMs', 0))}, "
                      f"turned around {sum((today.get('blocked') or {}).values())} times, {today.get('tasksDone', 0)} tasks done.")
        hits = self.kb.search(q, k=3)
        sources = [{"title": h["title"] + (f" › {h['section']}" if h["section"] else ""), "source": h["path"], "kind": h["kind"]} for h in hits]
        o = self.cfg["ollama"]
        if o.get("enabled"):
            notes = "\n\n".join(f"[{h['path']}] {h['title']} {h['section']}\n{rag.first_sentences(h['text'], 4, 600)}" for h in hits)
            prompt = (
                "You are FocusGuard AI, a warm, direct coach that helps the user stop doom-scrolling and focus on what matters. "
                "Answer the question in at most 120 words, using only the data and notes below. Use exact numbers from the data. "
                "If you use a note, mention its title. If the data can't answer it, say so and suggest one concrete next step.\n\n"
                f"DATA\n{today_line}\nThis week: {week['headline']}. " + " ".join(week.get("insights", [])) +
                f"\n\nNOTES\n{notes or '(none)'}\n\nQUESTION\n{q}"
            )
            try:
                req = urllib.request.Request(
                    o["url"].rstrip("/") + "/api/generate",
                    data=json.dumps({"model": o["model"], "prompt": prompt, "stream": False}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=max(o.get("timeout_s", 4), 30)) as r:
                    text = json.loads(r.read()).get("response", "").strip()
                if text:
                    return {"answer": text, "sources": sources, "source": "ollama"}
            except Exception:
                pass
        # Without a model: answer from the numbers, then point to the best-matching notes.
        ql = q.lower()
        parts = [today_line] if any(w in ql for w in ("today", "now", "so far")) else \
                [week["headline"] + "."] + week.get("insights", [])[:3]
        if hits:
            best = hits[0]
            body = best["sections"].get("How to do it") or best["text"]
            parts.append(f"From your notes, {best['title']}: {rag.first_sentences(body, 2)}")
        else:
            parts.append(week.get("suggestion", ""))
        return {"answer": "\n\n".join(p for p in parts if p), "sources": sources, "source": "rules"}

    # -- Streamlit dashboard -----------------------------------------------------
    DASHBOARD_PORT = 8501

    def dashboard_url(self) -> str:
        return f"http://127.0.0.1:{self.DASHBOARD_PORT}"

    def start_dashboard(self) -> dict:
        """Start the Streamlit dashboard if it isn't running yet."""
        with socket.socket() as s:
            s.settimeout(0.3)
            if s.connect_ex(("127.0.0.1", self.DASHBOARD_PORT)) == 0:
                return {"ok": True, "url": self.dashboard_url(), "started": False}
        app = collector.ROOT / "app" / "dashboard.py"
        try:
            import importlib.util
            if importlib.util.find_spec("streamlit") is None:
                return {"ok": False, "error": "Streamlit isn't installed. Run: .venv/bin/pip install streamlit"}
            self._dash = subprocess.Popen(
                [sys.executable, "-m", "streamlit", "run", str(app), "--server.headless", "true",
                 "--server.port", str(self.DASHBOARD_PORT), "--server.address", "127.0.0.1",
                 "--browser.gatherUsageStats", "false"],
                cwd=str(collector.ROOT), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "url": self.dashboard_url(), "started": True}

    def open_dashboard(self):
        r = self.start_dashboard()
        if not r["ok"]:
            notify("FocusGuard AI", r["error"])
            return r
        if r["started"]:
            time.sleep(3)
        webbrowser.open(r["url"])
        return r

    # -- AI task breakdown -------------------------------------------------------
    def breakdown(self, task: str) -> dict:
        task = " ".join(str(task or "").split())[:200]
        if not task:
            return {"steps": [], "source": "none"}
        o = self.cfg["ollama"]
        if o.get("enabled"):
            prompt = (
                "Break this task into 3 to 6 concrete steps for someone who tends to procrastinate. "
                "Each step must be doable in one 25-minute focus session, start with a verb, and be under 70 characters. "
                "Make the first step tiny so it is easy to start. "
                'Return JSON: {"steps": ["..."]}\n\nTask: ' + task
            )
            try:
                req = urllib.request.Request(
                    o["url"].rstrip("/") + "/api/generate",
                    data=json.dumps({"model": o["model"], "prompt": prompt, "stream": False, "format": "json"}).encode(),
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=max(o.get("timeout_s", 4), 20)) as r:
                    steps = json.loads(json.loads(r.read()).get("response", "{}")).get("steps", [])
                steps = [" ".join(str(x).split())[:90] for x in steps if str(x).strip()][:6]
                if len(steps) >= 2:
                    return {"steps": steps, "source": "ollama"}
            except Exception:
                pass
        return {"steps": [
            f"Write one sentence on what “done” means for: {task[:60]}",
            "Gather what you need: files, links, notes",
            "Do the smallest visible piece in one 25-minute session",
            "Review it and write down the next step",
        ], "source": "template"}

    # -- extension state + commands (the hub the MCP server talks to) -------------
    def save_ext_state(self, payload: dict):
        state = {k: payload.get(k) for k in ("days", "tasks", "rules", "focus", "focusSettings", "profile")}
        if not isinstance(state["days"], dict):
            raise ValueError("days must be an object")
        state["updated_at"] = now_ms()
        self._set("ext_state", state)
        if isinstance(state.get("profile"), dict):
            self._write_profile(state["profile"], state.get("rules") or [])

    def _write_profile(self, p: dict, rules: list[dict]):
        """Your onboarding answers as Markdown, so the coach and search can use them."""
        from_ids = lambda ids, opts: [o for i, o in opts.items() if i in (ids or [])]
        labels = {r["id"]: r["label"] for r in rules}
        goals = {"study": "Studies and exams", "career": "Job search or career", "project": "A project I care about",
                 "sleep": "Better sleep", "health": "Exercise and health", "people": "Time with family and friends",
                 "reading": "Reading and learning", "create": "Making things: art, music, writing"}
        times = {"morning": "mornings", "afternoon": "afternoons", "evening": "evenings", "late": "late at night",
                 "bed": "in bed before sleep", "work": "while I should be working or studying"}
        trig = {"bored": "boredom", "avoiding": "avoiding something hard", "stress": "stress or anxiety",
                "tired": "being tired", "habit": "pure habit", "fomo": "not wanting to miss out"}
        lines = ["# About me", ""]
        if p.get("name"):
            lines += ["## Name", str(p["name"])[:60], ""]
        g = from_ids(p.get("goals"), goals)
        if p.get("mainGoal") or g:
            lines += ["## What matters most right now and what I want time back for"]
            if p.get("mainGoal"):
                lines.append(f"My main goal: {str(p['mainGoal'])[:200]}.")
            if g:
                lines.append("I want the time for: " + ", ".join(g).lower() + ".")
            lines.append("")
        s = [labels.get(i, i) for i in p.get("sites") or []] + ([p["otherSite"]] if p.get("otherSite") else [])
        if s:
            lines += ["## Where I lose time", "I lose the most time on " + ", ".join(s) + ".", ""]
        t = from_ids(p.get("times"), times)
        if t:
            lines += ["## My weak moments", "It usually happens " + ", ".join(t) + ".", ""]
        tr = from_ids(p.get("triggers"), trig)
        if tr:
            lines += ["## Why I scroll", "What pulls me in: " + ", ".join(tr) + ".", ""]
        text = "\n".join(lines) + "\n"
        try:
            self.profile_dir.mkdir(parents=True, exist_ok=True)
            f = self.profile_dir / "profile.md"
            if not f.exists() or f.read_text() != text:
                f.write_text(text)
        except OSError:
            pass

    def queue_command(self, ctype: str, args: dict | None = None) -> dict:
        if ctype not in COMMAND_TYPES:
            raise ValueError(f"unknown command {ctype}")
        args = args or {}
        if ctype == "set_site_mode" and args.get("mode") not in SITE_MODES:
            raise ValueError("mode must be off, track, limit or block")
        cmd = {"id": str(uuid.uuid4()), "type": ctype, "args": args, "created_at": now_ms()}
        with self.lock:
            cmds = self._get("commands", []) or []
            cmds.append(cmd)
            self._set("commands", cmds[-50:])
        return cmd

    def _take_commands(self) -> list[dict]:
        with self.lock:
            cmds = self._get("commands", []) or []
            if cmds:
                self._set("commands", [])
        cutoff = now_ms() - 10 * 60000  # stale commands (browser was closed) are dropped
        return [c for c in cmds if c["created_at"] >= cutoff]

    def _baseline(self, sessions: list[dict], t: int):
        bc = self.cfg.get("baseline") or {}
        if not bc.get("enabled", True):
            return None
        ext = self._get("ext_state", {}) or {}
        days = ext.get("days") or {}
        live = [s for s in sessions if s["status"] == "open"]
        if not days or not live:
            return None
        lt = time.localtime(t / 1000)
        today_key = time.strftime("%Y-%m-%d", lt)
        live_ms = sum(s["duration_s"] * 1000 for s in live)
        b = detector.above_baseline(days, today_key, lt.tm_hour, live_ms, window_hours=bc.get("window_hours", 3),
                                    factor=bc.get("factor", 2.0), floor_minutes=bc.get("floor_minutes", 20))
        return detector.baseline_alert(b, live[-1]["app"], live[-1]["session_id"]) if b else None

    def handle_post(self, path, payload):
        if path == "/api/coach":
            return 200, self.coach(payload)
        if path == "/api/state":
            try:
                self.save_ext_state(payload)
            except ValueError as e:
                return 400, {"error": str(e)}
            return 200, {"ok": True, "commands": self._take_commands()}
        if path == "/api/command":
            try:
                return 200, self.queue_command(payload.get("type"), payload.get("args"))
            except ValueError as e:
                return 400, {"error": str(e)}
        if path == "/api/breakdown":
            return 200, self.breakdown(payload.get("task"))
        if path == "/api/ask":
            return 200, self.ask(payload.get("question"))
        if path == "/api/plan":
            with self._agent_busy:
                return 200, self.agent.make_plan(reason="asked")
        if path == "/api/agent/chat":
            hist = payload.get("history") if isinstance(payload.get("history"), list) else []
            with self._agent_busy:
                return 200, self.agent.chat(payload.get("message"), hist)
        if path == "/api/dashboard":
            return 200, self.start_dashboard()
        if path == "/api/rescue":
            if payload.get("stop"):
                self.end_rescue("ended_early")
            else:
                self.start_rescue(payload.get("minutes"))
            return 200, self.control()
        if path == "/api/pause":
            self.pause(float(payload.get("minutes", 60)))
            return 200, self.control()
        return None

    # -- the AI agent's bookkeeping ---------------------------------------------
    def save_plan(self, plan: dict):
        self._set("plan", plan)
        self._changed()

    def plan_summary(self) -> dict | None:
        p = self._get("plan", {}) or {}
        if p.get("date") != time.strftime("%Y-%m-%d"):
            return None
        return {k: p.get(k) for k in ("date", "message", "first_task", "focus_blocks", "site_changes", "source")}

    def remember_temp_change(self, change: dict):
        today = time.strftime("%Y-%m-%d")
        temp = self._get("temp_changes", {}) or {}
        if temp.get("date") != today:
            temp = {"date": today, "changes": []}
        if not any(c["site"] == change["site"] for c in temp["changes"]):
            temp["changes"].append({"site": change["site"], "label": change["label"],
                                    "from": change["from"], "from_limit": change.get("from_limit")})
        self._set("temp_changes", temp)

    def log_agent(self, kind: str, message: str, actions: list[str], source: str):
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO agent_log (ts, kind, message, actions, source) VALUES (?, ?, ?, ?, ?)",
                              (now_ms(), kind, (message or "")[:1000], json.dumps(actions or []), source))

    def _create_alert(self, kind, app, headline, explanation, stats) -> str:
        aid = str(uuid.uuid4())
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO alerts (id, created_at, kind, app, session_id, headline, explanation, stats, level)
                   VALUES (?, ?, ?, ?, NULL, ?, ?, ?, 1)""",
                (aid, now_ms(), kind, app, headline, explanation, json.dumps(stats)))
        self._changed()
        return aid

    def _plan_block_from_alert(self, alert_id: str, status: str):
        p = self._get("plan", {}) or {}
        with self.lock:
            row = self.conn.execute("SELECT stats FROM alerts WHERE id=?", (alert_id,)).fetchone()
        try:
            block_id = json.loads(row["stats"] or "{}").get("block_id") if row else None
        except ValueError:
            block_id = None
        if block_id:
            for b in p.get("focus_blocks") or []:
                if b["id"] == block_id:
                    b["status"] = status
            self._set("plan", p)

    def _run_agent_async(self, fn, *args):
        def work():
            if not self._agent_busy.acquire(blocking=False):
                return
            try:
                fn(*args)
            except Exception as e:
                print(f"agent error: {e}", file=sys.stderr)
            finally:
                self._agent_busy.release()
        threading.Thread(target=work, daemon=True, name="agent").start()

    def _agent_duties(self, t: int, ctl: dict):
        """What the agent does on its own: undo yesterday's temporary changes, make
        today's plan once you're at the computer, and prompt planned sessions."""
        if not self.cfg.get("agent", {}).get("enabled", True):
            return
        today = time.strftime("%Y-%m-%d")
        lt = time.localtime(t / 1000)
        temp = self._get("temp_changes", {}) or {}
        if temp.get("date") and temp["date"] != today and temp.get("changes"):
            for c in temp["changes"]:
                args = {"site": c["site"], "mode": c["from"]}
                if c.get("from_limit"):
                    args["limit_minutes"] = c["from_limit"]
                self.queue_command("set_site_mode", args)
            self.log_agent("revert", "Yesterday's temporary limits are back to your own settings.",
                           [f"{c['label']}: back to {c['from']}" for c in temp["changes"]], "rules")
            self._set("temp_changes", {})

        ext = self._get("ext_state", {}) or {}
        active = t - (ext.get("updated_at") or 0) < 15 * 60000
        has_material = bool([x for x in ext.get("tasks") or [] if not x.get("done")] or (ext.get("profile") or {}).get("completedAt"))
        plan = self._get("plan", {}) or {}
        if (self.cfg.get("agent", {}).get("daily_plan", True) and active and has_material
                and plan.get("date") != today and lt.tm_hour >= 5):
            def daily():
                p = self.agent.make_plan(reason="daily")
                notify("Your plan for today is ready", (p.get("message") or "")[:180])
            self._set("plan", {**plan, "date": today, "pending": True})  # don't start twice
            self._run_agent_async(daily)

        if ctl["paused_until"]:
            return
        plan = self._get("plan", {}) or {}
        if plan.get("date") != today:
            return
        now_min = lt.tm_hour * 60 + lt.tm_min
        changed = False
        for b in plan.get("focus_blocks") or []:
            start = planner._hhmm_to_min(b.get("time"))
            if b.get("status") != "planned" or start is None or now_min < start:
                continue
            if now_min - start > 30:
                b["status"] = "missed"
            elif not ctl["rescue_until"] and not self._get("focus_until") > t:
                task = b.get("task")
                self._create_alert(
                    "planned_focus", "driftguard",
                    f"Your planned focus starts now" + (f": {task}" if task else ""),
                    f"You planned {b['minutes']} minutes" + (f" on “{task}”" if task else "") + " for now. Everything distracting stays closed while it runs.",
                    {"action": "focus", "task": task, "minutes": b["minutes"], "block_id": b["id"],
                     "cta": f"Start {b['minutes']} minutes"})
                self.log_agent("prompt", f"Prompted the {planner._ampm(b['time'])} focus session.", [], "rules")
                b["status"] = "prompted"
            changed = True
        if changed:
            self._set("plan", plan)

    def _check_struggle(self):
        """Several blocked-site attempts in a short time: let the agent step in."""
        cfg = self.cfg.get("agent", {})
        if not cfg.get("enabled", True):
            return
        t = now_ms()
        window = cfg.get("struggle_window_minutes", 20) * 60000
        if t - (self._get("last_intervene") or 0) < cfg.get("intervene_cooldown_minutes", 30) * 60000:
            return
        ctl = self.control()
        if ctl["paused_until"] or ctl["rescue_until"] or self._get("focus_until") > t:
            return
        with self.lock:
            rows = self.conn.execute("SELECT app FROM browser_events WHERE type='blocked' AND ts >= ?", (t - window,)).fetchall()
        if len(rows) < cfg.get("struggle_blocks", 3):
            return
        today_start = int(time.mktime(time.localtime(t / 1000)[:3] + (0, 0, 0, 0, 0, -1)) * 1000)
        with self.lock:
            autonomous_today = self.conn.execute(
                "SELECT COUNT(*) FROM agent_log WHERE kind='intervene' AND ts >= ?", (today_start,)).fetchone()[0]
        if autonomous_today >= int(cfg.get("max_autonomous_interventions_per_day", 6)):
            self.log_agent("safety", "Autonomous intervention budget reached; waiting for the user's next action.", [], "rules")
            return
        self._set("last_intervene", t)
        ext = self._get("ext_state", {}) or {}
        labels = {r["id"]: r["label"] for r in ext.get("rules") or []}
        apps = sorted({labels.get(r["app"], r["app"]) for r in rows})
        detail = f"{len(rows)} times in {window // 60000} minutes ({', '.join(apps)})"

        def step_in():
            r = self.agent.intervene(detail)
            o = r["offer"]
            stats = {"action": o["action"], "task": o.get("task"), "minutes": o.get("minutes", 10),
                     "cta": (f"Start {o.get('minutes', 10)} minutes" + (" on it" if o.get("task") else "")) if o["action"] == "focus"
                            else "Start a 10-minute reset"}
            if o["action"] != "focus":
                stats["action"] = "away"
            self._create_alert("agent", "driftguard", "Let's break this loop", o["message"], stats)
        self._run_agent_async(step_in)

    # -- status ---------------------------------------------------------------
    def status(self) -> dict:
        t = now_ms()
        midnight = int(time.mktime(time.localtime()[:3] + (0, 0, 0, 0, 0, -1)) * 1000)
        sessions = collector.build_sessions(self.conn, since_ms=midnight, now_ms=t)
        by_app: dict[str, int] = {}
        for s in sessions:
            by_app[s["app"]] = by_app.get(s["app"], 0) + s["duration_s"]
        with self.lock:
            alerts = [dict(r) for r in self.conn.execute(
                "SELECT * FROM alerts WHERE created_at >= ? ORDER BY created_at DESC", (midnight,))]
        exits = sum(1 for a in alerts if a["outcome"] in ("left", "rescued", "rescue_completed", "dismissed"))
        current = next((s for s in reversed(sessions) if s["status"] == "open"), None)
        ext = self._get("ext_state", {}) or {}
        profile = ext.get("profile") or {}
        alignment = detector.intent_mismatch(profile, current)
        drift = detector.drift_score(current, {"intent_mismatch": alignment["mismatch"]})
        if current:
            drift["reason"] = alignment["reason"]
        latest = alerts[0] if alerts else None
        learning = self._adaptive_plan(latest["kind"], latest["app"]) if latest else detector.adaptive_intervention([])
        return {
            "now": t,
            "control": self.control(),
            "today": {
                "minutes_by_app": {k: round(v / 60) for k, v in sorted(by_app.items(), key=lambda x: -x[1])},
                "shorts": sum(s["shorts_viewed"] for s in sessions),
                "opens": len(sessions),
                "nudges": len(alerts),
                "exits": exits,
                "rescues_completed": sum(1 for a in alerts if a["outcome"] == "rescue_completed"),
            },
            "current": current,
            "drift": drift,
            "learning": learning,
            "alerts": alerts[:10],
        }

    def pair(self, origin):
        # Automatic pairing: the first browser extension to ask gets the token.
        # After that, pairing reopens only when you choose "Pair a browser" in the tray.
        if not self._get("paired", False) or self._get("pair_until") > now_ms():
            self._set("paired", True)
            self._set("pair_until", 0)
            return True
        return False

    def open_pairing(self, minutes: int = 5):
        self._set("pair_until", now_ms() + minutes * 60000)

    def page(self, path):
        if path not in ("/", "/index.html"):
            return None
        return render_status_page(self.status(), self.token)

    # -- loop -----------------------------------------------------------------
    def run_forever(self):
        # Run immediately after startup, then on the configured heartbeat.
        while not self._stop.is_set():
            try:
                until = self._get("rescue_until")
                if until and until <= now_ms():
                    self.end_rescue("completed")  # timer ran out with no word from the browser
                self.tick()
            except Exception as e:
                print(f"tick error: {e}", file=sys.stderr)
                previous = self._get("runtime", {}) or {}
                self._runtime_update(last_heartbeat=now_ms(), consecutive_errors=int(previous.get("consecutive_errors") or 0) + 1,
                                     last_error=str(e)[:500])
                self.log_agent("error", "The agent recovered from a tick failure.", [str(e)[:500]], "runtime")
            self._stop.wait(max(1, int(self.cfg.get("tick_seconds", 10))))

    def stop(self):
        self._stop.set()


NICE = {"youtube_shorts": "YouTube Shorts", "instagram_reels": "Instagram Reels", "x": "X", "tiktok": "TikTok"}
OUTCOME_LABEL = {
    None: "waiting", "left": "you left", "stayed": "you stayed", "snoozed": "snoozed",
    "dismissed": "dismissed", "rescued": "rescue started", "rescue_completed": "rescue finished",
    "rescue_ended_early": "rescue ended early",
}


def hm(ms: int) -> str:
    return time.strftime("%I:%M %p", time.localtime(ms / 1000)).lstrip("0")


def render_status_page(st: dict, token: str) -> str:
    esc = html.escape
    today = st["today"]
    ctl = st["control"]
    apps = "".join(
        f"<tr><td>{esc(NICE.get(a, a.replace('_', ' ').title()))}</td><td>{m} min</td></tr>"
        for a, m in today["minutes_by_app"].items()) or '<tr><td colspan="2" class="muted">Nothing tracked yet today</td></tr>'
    alerts = "".join(
        f"<li><b>{esc(a['headline'])}</b><br><span class='muted'>{hm(a['created_at'])}"
        f" · {esc(OUTCOME_LABEL.get(a['outcome'], a['outcome']))}</span></li>"
        for a in st["alerts"]) or "<li class='muted'>No nudges today</li>"
    if ctl["rescue_until"]:
        state = f"Focus Rescue until {hm(ctl['rescue_until'])}"
    elif ctl["paused_until"]:
        state = f"Paused until {hm(ctl['paused_until'])}"
    else:
        state = "Watching"
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>FocusGuard AI</title><meta http-equiv="refresh" content="30">
<style>
:root{{--bg:#faf8f5;--fg:#1f1d1a;--muted:#6b665e;--line:#e4dfd7;--accent:#2f6f5e}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1b1a18;--fg:#ece8e1;--muted:#9a948a;--line:#34312c;--accent:#7cc4ad}}}}
body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}}
main{{max-width:640px;margin:32px auto;padding:0 16px}}
h1{{font-size:22px;margin:0}} .state{{color:var(--accent);font-weight:600}}
.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin:20px 0}}
.card{{border:1px solid var(--line);border-radius:10px;padding:12px}} .n{{font-size:24px;font-weight:650}}
.muted{{color:var(--muted);font-size:13px}} table{{width:100%;border-collapse:collapse}}
td{{padding:6px 0;border-bottom:1px solid var(--line)}} td:last-child{{text-align:right}}
ul{{list-style:none;padding:0}} li{{padding:8px 0;border-bottom:1px solid var(--line)}}
button{{padding:8px 14px;border-radius:8px;border:1px solid var(--line);background:var(--fg);color:var(--bg);font:inherit;cursor:pointer;margin-right:6px}}
button.s{{background:transparent;color:var(--fg)}}
@media (max-width:520px){{.grid{{grid-template-columns:repeat(2,1fr)}}}}
</style></head><body><main>
<h1>FocusGuard AI</h1><div class="state">{esc(state)}</div>
<div class="grid">
<div class="card"><div class="n">{today['shorts']}</div><div class="muted">shorts today</div></div>
<div class="card"><div class="n">{today['opens']}</div><div class="muted">opens</div></div>
<div class="card"><div class="n">{today['nudges']}</div><div class="muted">nudges</div></div>
<div class="card"><div class="n">{today['exits']}</div><div class="muted">exits</div></div>
</div>
<p><button onclick="act('/api/rescue',{{}})">Start Focus Rescue</button>
<button class="s" onclick="act('/api/pause',{{minutes:60}})">Pause 1 hour</button>
<button class="s" onclick="act('/api/pause',{{minutes:0}})">Resume</button></p>
<h3>Time today</h3><table>{apps}</table>
<h3>Nudges</h3><ul>{alerts}</ul>
<p class="muted">For charts, tasks and the coach, open the <a href="#" onclick="dash();return false">full dashboard</a>. Everything here stays on this computer.</p>
</main><script>
async function dash(){{const r=await fetch('/api/dashboard',{{method:'POST',headers:{{'Content-Type':'application/json','X-DriftGuard-Token':{json.dumps(token)}}},body:'{{}}'}});const j=await r.json();if(j.ok)setTimeout(()=>location.href=j.url,j.started?3000:0);else alert(j.error)}}
async function act(p,b){{await fetch(p,{{method:'POST',headers:{{'Content-Type':'application/json','X-DriftGuard-Token':{json.dumps(token)}}},body:JSON.stringify(b)}});location.reload()}}
</script></body></html>"""


def main():
    cfg = load_config()
    conn = collector.open_db()
    token = collector.load_token()
    engine = Engine(conn, cfg, token)
    try:
        server = collector.serve(conn=conn, hooks=engine)
    except OSError:
        print("FocusGuard AI is already running (port 8765 is taken).")
        webbrowser.open(f"http://{collector.HOST}:{collector.PORT}/")
        return
    threading.Thread(target=server.serve_forever, daemon=True, name="http").start()
    threading.Thread(target=engine.run_forever, daemon=True, name="engine").start()
    url = f"http://{collector.HOST}:{collector.PORT}/"
    print(f"FocusGuard AI running · status page {url}")
    print(f"Extension token: {token}")

    headless = "--no-tray" in sys.argv or os.environ.get("DRIFTGUARD_NO_TRAY")
    if not headless:
        try:
            import tray
            tray.run(engine, url)  # blocks on the main thread (required on macOS)
            return
        except ImportError:
            print("Tip: `pip install pystray pillow` for a menu-bar / tray icon.")
        except Exception as e:  # e.g. no display available
            print(f"Tray icon unavailable ({e.__class__.__name__}); running in the background without it.")
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        pass
    finally:
        engine.stop()
        server.shutdown()


if __name__ == "__main__":
    main()
