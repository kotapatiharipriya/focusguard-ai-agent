"""FocusGuard AI browser-event collector.

Receives batched events from the FocusGuard AI browser extension, validates them,
and stores them in SQLite. Also rebuilds those raw events into per-visit
sessions that the deterministic detectors (doom-scrolling, app-hopping,
repeated reopening) can consume in place of the simulated events.

Run standalone:
    python3 agent/collector.py            # listens on 127.0.0.1:8765

Or use from personal_agent.py:
    from collector import open_db, ingest_batch, build_sessions

Stdlib only. Binds to localhost only. Never stores URLs or titles — the
extension doesn't send them, and the validator rejects unknown fields' values
by simply not storing them.
"""

from __future__ import annotations

import hmac
import json
import os
import re
import secrets
import sqlite3
import statistics
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = Path(os.environ.get("DRIFTGUARD_DB", ROOT / "data" / "driftguard.db"))
TOKEN_FILE = Path(os.environ.get("DRIFTGUARD_TOKEN_FILE", ROOT / "data" / ".collector_token"))
HOST = "127.0.0.1"
PORT = int(os.environ.get("DRIFTGUARD_COLLECTOR_PORT", "8765"))

EVENT_TYPES = {
    "session_start", "session_end", "heartbeat", "short_viewed", "scroll_burst", "pause",
    # Redirect / Protect / Recover, reported back by the extension
    "nudge_response", "focus_rescue_start", "focus_rescue_end",
    # Blocking and focus sessions from the extension
    "blocked", "focus_start", "focus_end", "task_done", "pass_used",
}
INT_FIELDS = ("duration_ms", "dwell_ms", "scroll_px", "wheel_ticks", "key_nav")
TEXT_FIELDS = {"surface": 24, "reason": 32}
ID_RE = re.compile(r"^[A-Za-z0-9-]{8,64}$")
APP_RE = re.compile(r"^[a-z0-9_]{1,40}$")
MAX_BATCH = 500
MAX_BODY = 512 * 1024
MAX_INT = 10**9
CLOCK_SKEW_MS = 7 * 24 * 3600 * 1000
STALE_AFTER_MS = 3 * 60 * 1000
DB_LOCK = threading.RLock()  # one SQLite connection is shared by the server and the engine  # no heartbeat for 3 min => session considered over

SCHEMA = """
CREATE TABLE IF NOT EXISTS browser_events (
    id          TEXT PRIMARY KEY,
    ts          INTEGER NOT NULL,          -- epoch ms, from the browser
    type        TEXT NOT NULL,
    app         TEXT NOT NULL,
    surface     TEXT,
    session_id  TEXT,
    duration_ms INTEGER,
    dwell_ms    INTEGER,
    scroll_px   INTEGER,
    wheel_ticks INTEGER,
    key_nav     INTEGER,
    reason      TEXT,
    ref         TEXT,                     -- alert id a response refers to
    received_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_browser_events_ts ON browser_events(ts);
CREATE INDEX IF NOT EXISTS idx_browser_events_session ON browser_events(session_id);
"""


# ---------------------------------------------------------------------------
# Storage

def open_db(path: Path | str = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(browser_events)")}
    if "ref" not in cols:  # databases created by v0.1
        conn.execute("ALTER TABLE browser_events ADD COLUMN ref TEXT")
    return conn


def load_token() -> str:
    env = os.environ.get("DRIFTGUARD_TOKEN")
    if env:
        return env
    if TOKEN_FILE.exists():
        return TOKEN_FILE.read_text().strip()
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(24)
    TOKEN_FILE.write_text(token)
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    return token


# ---------------------------------------------------------------------------
# Validation + ingest

def _clean_event(raw: dict, now_ms: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    eid, etype, app, ts = raw.get("id"), raw.get("type"), raw.get("app"), raw.get("ts")
    if not (isinstance(eid, str) and ID_RE.match(eid)):
        return None
    if etype not in EVENT_TYPES:
        return None
    if not (isinstance(app, str) and APP_RE.match(app)):
        return None
    if not isinstance(ts, (int, float)) or abs(int(ts) - now_ms) > CLOCK_SKEW_MS:
        return None

    ev = {"id": eid, "type": etype, "app": app, "ts": int(ts)}
    sid = raw.get("session_id")
    ev["session_id"] = sid if isinstance(sid, str) and ID_RE.match(sid) else None
    ref = raw.get("ref")
    ev["ref"] = ref if isinstance(ref, str) and ID_RE.match(ref) else None
    for f in INT_FIELDS:
        v = raw.get(f)
        ev[f] = max(0, min(int(v), MAX_INT)) if isinstance(v, (int, float)) and v == v else None
    for f, maxlen in TEXT_FIELDS.items():
        v = raw.get(f)
        ev[f] = v[:maxlen] if isinstance(v, str) and re.fullmatch(r"[a-z_]*", v[:maxlen]) else None
    return ev


def ingest_batch(conn: sqlite3.Connection, payload: dict) -> dict:
    """Validate and store a batch. Idempotent: resent events are ignored by id."""
    events = payload.get("events") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        raise ValueError("payload must be an object with an 'events' list")
    if len(events) > MAX_BATCH:
        raise ValueError(f"batch too large (max {MAX_BATCH})")

    now_ms = int(time.time() * 1000)
    clean = [e for e in (_clean_event(r, now_ms) for r in events) if e]
    with DB_LOCK, conn:
        before = conn.total_changes
        conn.executemany(
            """INSERT OR IGNORE INTO browser_events
               (id, ts, type, app, surface, session_id, duration_ms, dwell_ms,
                scroll_px, wheel_ticks, key_nav, reason, ref, received_at)
               VALUES (:id, :ts, :type, :app, :surface, :session_id, :duration_ms, :dwell_ms,
                       :scroll_px, :wheel_ticks, :key_nav, :reason, :ref, :received_at)""",
            [{**e, "received_at": now_ms} for e in clean],
        )
        inserted = conn.total_changes - before
    return {
        "accepted": inserted,
        "duplicates": len(clean) - inserted,
        "rejected": len(events) - len(clean),
        "_events": clean,  # for hooks; stripped before the HTTP reply
    }


# ---------------------------------------------------------------------------
# Sessions: the shape the detectors want

def build_sessions(conn: sqlite3.Connection, since_ms: int = 0, now_ms: int | None = None) -> list[dict]:
    """Collapse raw events into one row per visit to a tracked app.

    Each session carries the signals the deterministic rules need:
    duration, shorts swiped and how fast, scrolling volume, and how soon
    you came back to the same app after last leaving it.
    """
    now_ms = now_ms or int(time.time() * 1000)
    with DB_LOCK:
        rows = conn.execute(
            "SELECT * FROM browser_events WHERE ts >= ? AND session_id IS NOT NULL ORDER BY ts",
            (since_ms,),
        ).fetchall()

    by_sid: dict[str, dict] = {}
    for r in rows:
        s = by_sid.setdefault(r["session_id"], {
            "session_id": r["session_id"], "app": r["app"], "surfaces": set(),
            "start": None, "end": None, "last_seen": r["ts"], "ended_by": None,
            "dwells": [], "scroll_px": 0, "wheel_ticks": 0, "key_nav": 0, "scroll_bursts": 0,
        })
        s["last_seen"] = max(s["last_seen"], r["ts"])
        if r["surface"]:
            s["surfaces"].add(r["surface"])
        t = r["type"]
        if t == "session_start":
            s["start"] = r["ts"]
        elif t == "session_end":
            s["end"] = r["ts"]
            s["ended_by"] = r["reason"]
        elif t == "short_viewed" and r["dwell_ms"] is not None:
            s["dwells"].append(r["dwell_ms"])
        elif t == "scroll_burst":
            s["scroll_px"] += r["scroll_px"] or 0
            s["wheel_ticks"] += r["wheel_ticks"] or 0
            s["key_nav"] += r["key_nav"] or 0
            s["scroll_bursts"] += 1

    sessions = []
    for s in by_sid.values():
        start = s["start"] if s["start"] is not None else min(s["last_seen"], s["end"] or s["last_seen"])
        if s["end"] is not None:
            end, status = s["end"], "closed"
        elif now_ms - s["last_seen"] > STALE_AFTER_MS:
            end, status = s["last_seen"], "stale"   # browser closed/crashed mid-session
        else:
            end, status = now_ms, "open"
        minutes = max((end - start) / 60000, 1 / 60)
        dw = s["dwells"]
        sessions.append({
            "session_id": s["session_id"],
            "app": s["app"],
            "surfaces": sorted(s["surfaces"]),
            "started_at": start,
            "ended_at": end,
            "status": status,
            "ended_by": s["ended_by"],
            "duration_s": round((end - start) / 1000),
            "shorts_viewed": len(dw),
            "shorts_per_min": round(len(dw) / minutes, 2),
            "median_short_dwell_s": round(statistics.median(dw) / 1000, 1) if dw else None,
            "scroll_px": s["scroll_px"],
            "scroll_px_per_min": round(s["scroll_px"] / minutes),
            "wheel_ticks": s["wheel_ticks"],
            "key_nav": s["key_nav"],
        })

    sessions.sort(key=lambda x: x["started_at"])
    last_end_by_app: dict[str, int] = {}
    prev_app = None
    for s in sessions:
        prev_end = last_end_by_app.get(s["app"])
        s["reopened_after_s"] = round((s["started_at"] - prev_end) / 1000) if prev_end else None
        s["came_from_app"] = prev_app
        last_end_by_app[s["app"]] = s["ended_at"]
        prev_app = s["app"]
    return sessions


# ---------------------------------------------------------------------------
# HTTP server

def _is_allowed_origin(origin: str | None, self_origin: str = "") -> bool:
    # No Origin: curl / scripts / plain navigation. Extension origins: the collector.
    # Our own status page is allowed too. Any other web page is refused, so a
    # random site can't read from or write to your backend.
    if origin is None or (self_origin and origin == self_origin):
        return True
    return origin.startswith(("chrome-extension://", "moz-extension://"))


class Hooks:
    """Extension points used by the FocusGuard AI engine (agent/driftguard.py).
    The bare collector uses these no-op defaults."""

    def after_ingest(self, events: list[dict]) -> dict:
        return {}

    def handle_get(self, path: str, query: dict) -> tuple[int, dict] | None:
        return None

    def handle_post(self, path: str, payload: dict) -> tuple[int, dict] | None:
        return None

    def page(self, path: str) -> str | None:
        return None

    def pair(self, origin: str | None) -> bool:
        """May this browser extension be handed the token automatically?"""
        return False


def make_handler(conn: sqlite3.Connection, token: str, hooks: Hooks | None = None):
    hooks = hooks or Hooks()

    class Handler(BaseHTTPRequestHandler):
        server_version = "FocusGuard AI/0.2"

        @property
        def _self_origin(self) -> str:
            host, port = self.server.server_address[:2]
            return f"http://{host}:{port}"

        def _allowed(self, origin):
            return _is_allowed_origin(origin, self._self_origin) or origin == f"http://localhost:{self.server.server_address[1]}"

        def _send(self, code: int, body, content_type="application/json"):
            data = body.encode() if isinstance(body, str) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            origin = self.headers.get("Origin")
            if origin and self._allowed(origin):
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
            self.end_headers()
            self.wfile.write(data)

        def _token_ok(self) -> bool:
            return hmac.compare_digest(self.headers.get("X-DriftGuard-Token", ""), token)

        def _guard(self) -> bool:
            if not self._allowed(self.headers.get("Origin")):
                self._send(403, {"error": "origin not allowed"})
                return False
            return True

        def _body(self):
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > MAX_BODY:
                raise ValueError("body empty or too large")
            return json.loads(self.rfile.read(length))

        def do_OPTIONS(self):
            if not self._guard():
                return
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", self.headers.get("Origin", ""))
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-DriftGuard-Token")
            self.end_headers()

        def do_GET(self):
            if not self._guard():
                return
            url = urlparse(self.path)
            if not url.path.startswith("/api/"):
                html = hooks.page(url.path)
                if html is None:
                    return self._send(404, {"error": "not found"})
                return self._send(200, html, "text/html; charset=utf-8")
            if url.path == "/api/health":
                ok = self._token_ok()
                body = {"ok": True, "token_ok": ok}
                if ok:
                    with DB_LOCK:
                        body["events_total"] = conn.execute("SELECT COUNT(*) FROM browser_events").fetchone()[0]
                    runtime = getattr(hooks, "runtime_status", None)
                    if callable(runtime):
                        body["runtime"] = runtime()
                return self._send(200, body)
            if url.path == "/api/pair":
                # Extension service workers often send no Origin at all. Web pages always
                # do, and _guard() has already refused those; local programs could read
                # the token file anyway, so this is no weaker.
                origin = self.headers.get("Origin")
                if (origin is None or origin.startswith(("chrome-extension://", "moz-extension://"))) and hooks.pair(origin):
                    return self._send(200, {"token": token})
                return self._send(403, {"error": "pairing closed"})
            if not self._token_ok():
                return self._send(401, {"error": "bad token"})
            q = parse_qs(url.query)
            if url.path == "/api/browser/sessions":
                hours = float(q.get("hours", ["24"])[0])
                since = int(time.time() * 1000 - hours * 3600 * 1000)
                return self._send(200, {"sessions": build_sessions(conn, since)})
            if url.path == "/api/browser/events":
                limit = min(int(q.get("limit", ["100"])[0]), 1000)
                with DB_LOCK:
                    rows = conn.execute("SELECT * FROM browser_events ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()
                return self._send(200, {"events": [dict(r) for r in rows]})
            routed = hooks.handle_get(url.path, q)
            if routed:
                return self._send(*routed)
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._guard():
                return
            path = urlparse(self.path).path
            if not self._token_ok():
                return self._send(401, {"error": "bad token"})
            try:
                payload = self._body()
            except (ValueError, json.JSONDecodeError) as e:
                return self._send(400, {"error": str(e)})
            if path == "/api/events":
                try:
                    result = ingest_batch(conn, payload)
                except ValueError as e:
                    return self._send(400, {"error": str(e)})
                events = result.pop("_events")
                result.update(hooks.after_ingest(events))
                return self._send(200, result)
            routed = hooks.handle_post(path, payload)
            if routed:
                return self._send(*routed)
            self._send(404, {"error": "not found"})

        def log_message(self, fmt, *args):
            if os.environ.get("DRIFTGUARD_VERBOSE"):
                sys.stderr.write("driftguard: " + fmt % args + "\n")

    return Handler


def serve(db_path: Path | str = DEFAULT_DB, host: str = HOST, port: int = PORT,
          hooks: Hooks | None = None, conn: sqlite3.Connection | None = None) -> ThreadingHTTPServer:
    conn = conn or open_db(db_path)
    token = load_token()
    return ThreadingHTTPServer((host, port), make_handler(conn, token, hooks))


def main():
    server = serve()
    host, port = server.server_address[:2]
    print(f"FocusGuard AI collector listening on http://{host}:{port}")
    print(f"Database: {DEFAULT_DB}")
    print(f"Extension token: {load_token()}")
    print("Paste the token into the extension's Settings page. Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
