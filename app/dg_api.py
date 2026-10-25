"""Talks to the FocusGuard AI agent on this computer and shapes its data for the dashboard."""

from __future__ import annotations

import datetime as dt
import json
import os
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def base() -> str:
    return os.environ.get("DRIFTGUARD_URL", "http://127.0.0.1:8765").rstrip("/")

TOKEN_FILE = Path(os.environ.get("DRIFTGUARD_TOKEN_FILE", ROOT / "data" / ".collector_token"))
GOALS_FILE = ROOT / "knowledge" / "me" / "goals.md"

SHORT_VIDEO = {"youtube_shorts", "instagram_reels", "tiktok"}
REASONS = {"bored": "Bored", "avoiding": "Avoiding something", "tired": "Tired", "habit": "Just habit", "stress": "Stressed"}


class AgentDown(Exception):
    pass


def token() -> str:
    tok = os.environ.get("DRIFTGUARD_TOKEN")
    if tok:
        return tok
    try:
        return TOKEN_FILE.read_text().strip()
    except OSError:
        raise AgentDown("no token")


def call(method: str, path: str, body: dict | None = None, timeout: float = 15) -> dict:
    req = urllib.request.Request(
        base() + path, method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json", "X-DriftGuard-Token": token()})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        raise AgentDown(f"HTTP {e.code}")
    except (urllib.error.URLError, OSError) as e:
        raise AgentDown(str(e))


def command(ctype: str, **args) -> dict:
    return call("POST", "/api/command", {"type": ctype, "args": args})


# ---------------------------------------------------------------------------
# Shaping

def day_key(d: dt.date) -> str:
    return d.isoformat()


def last_days(n: int, end: dt.date | None = None) -> list[str]:
    end = end or dt.date.today()
    return [day_key(end - dt.timedelta(days=i)) for i in range(n - 1, -1, -1)]


def label_map(state: dict) -> dict:
    return {r["id"]: r["label"] for r in state.get("rules") or []}


def distract_ms(day: dict | None) -> float:
    return sum((a or {}).get("ms", 0) for a in ((day or {}).get("apps") or {}).values())


def totals(state: dict, keys: list[str]) -> dict:
    days = state.get("days") or {}
    labels = label_map(state)
    t = {"distract": 0, "focus": 0, "pomodoros": 0, "tasks": 0, "turned": 0, "shorts": 0, "passes": 0,
         "sites": {}, "reasons": {}}
    for k in keys:
        d = days.get(k) or {}
        for a, s in (d.get("apps") or {}).items():
            name = labels.get(a, a.replace("site_", "").replace("_", "."))
            x = t["sites"].setdefault(name, {"ms": 0, "visits": 0, "shorts": 0, "turned": 0})
            x["ms"] += s.get("ms", 0); x["visits"] += s.get("visits", 0); x["shorts"] += s.get("shorts", 0)
            t["distract"] += s.get("ms", 0); t["shorts"] += s.get("shorts", 0)
        for a, n in (d.get("blocked") or {}).items():
            name = labels.get(a, a.replace("site_", "").replace("_", "."))
            t["sites"].setdefault(name, {"ms": 0, "visits": 0, "shorts": 0, "turned": 0})["turned"] += n
            t["turned"] += n
        for r, n in (d.get("reasons") or {}).items():
            t["reasons"][r] = t["reasons"].get(r, 0) + n
        t["focus"] += d.get("focusMs", 0); t["pomodoros"] += d.get("pomodoros", 0)
        t["tasks"] += d.get("tasksDone", 0); t["passes"] += d.get("passes", 0)
    return t


def fmt(ms: float) -> str:
    m = round((ms or 0) / 60000)
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60:02d} min"


def fmt_short(ms: float) -> str:
    m = round((ms or 0) / 60000)
    return f"{m}m" if m < 60 else f"{m // 60}h {m % 60:02d}m"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def live_focus(state: dict, now_ms: float) -> dict | None:
    f = state.get("focus")
    if f and not f.get("completedAt") and f.get("until", 0) > now_ms:
        return f
    return None


def open_tasks(state: dict) -> list[dict]:
    """Open tasks with their open sub-steps right after them."""
    tasks = [t for t in state.get("tasks") or [] if not t.get("done")]
    ids = {t["id"] for t in tasks}
    out = []
    for t in tasks:
        if t.get("parentId") in ids:
            continue
        out.append(t)
        out += [c for c in tasks if c.get("parentId") == t["id"]]
    return out


def read_goals() -> str:
    try:
        return GOALS_FILE.read_text(encoding="utf-8")
    except OSError:
        return ""


def write_goals(text: str):
    GOALS_FILE.parent.mkdir(parents=True, exist_ok=True)
    GOALS_FILE.write_text(text, encoding="utf-8")
