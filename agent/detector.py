"""Deterministic doom-scrolling rules over browser sessions.

No LLM here: every decision is a threshold you can read and tune in
data/config.json. Each rule returns an Alert with a plain-language
explanation built from the actual numbers, so the nudge says *what*
is happening, not just "you've been scrolling a lot".
"""

from __future__ import annotations

from dataclasses import dataclass, field

SHORT_VIDEO_APPS = {"youtube_shorts", "instagram_reels", "tiktok"}

DEFAULTS = {
    # Fast-swipe loop on Shorts / Reels / TikTok
    "shorts_min_minutes": 8,
    "shorts_min_per_minute": 3.0,     # one every 20s or faster
    "shorts_hard_count": 40,          # this many in one sitting triggers regardless of pace
    # Endless feed scrolling (Reddit, X, Instagram feed…)
    "feed_min_minutes": 15,
    "feed_min_px_per_minute": 2500,
    # App-hopping: bouncing between distracting apps
    "hop_window_minutes": 10,
    "hop_min_switches": 5,
    "hop_min_apps": 3,
    # Repeated reopening: leaving and coming straight back
    "reopen_window_minutes": 30,
    "reopen_min_count": 3,
    "reopen_max_gap_seconds": 300,
}


@dataclass
class Alert:
    kind: str                 # doom_scroll | feed_scroll | app_hopping | reopen_loop
    app: str
    session_id: str | None
    headline: str
    explanation: str
    stats: dict = field(default_factory=dict)


def drift_score(session: dict | None, context: dict | None = None) -> dict:
    """Return a transparent 0–100 estimate of how automatic the session is.

    This is deliberately not an opaque model. The browser sends coarse signals
    and the user can inspect every contribution. It is a state signal for the
    agent, not a judgement about the person.
    """
    if not session:
        return {"score": 0, "label": "no active session", "reasons": []}
    context = context or {}
    score = 0
    reasons: list[str] = []
    minutes = max(0.0, float(session.get("duration_s", 0)) / 60)
    if minutes >= 5:
        points = min(20, round((minutes - 4) * 2))
        score += points
        reasons.append(f"{round(minutes)} minutes on the site")

    shorts = float(session.get("shorts_per_min", 0) or 0)
    if shorts >= 1:
        points = min(25, round(shorts * 7))
        score += points
        reasons.append(f"{shorts:g} short videos per minute")

    scroll = float(session.get("scroll_px_per_min", 0) or 0)
    if scroll >= 900:
        points = min(20, round(scroll / 300))
        score += points
        reasons.append("continuous feed scrolling")

    surfaces = set(session.get("surfaces") or [])
    if surfaces & {"shorts", "feed", "explore"}:
        score += 15
        reasons.append("an endless-feed surface")

    reopens = int(context.get("reopens", 0) or 0)
    if reopens:
        points = min(15, reopens * 5)
        score += points
        reasons.append(f"{reopens} quick return{'' if reopens == 1 else 's'}")

    switches = int(context.get("switches", 0) or 0)
    if switches:
        points = min(10, switches * 2)
        score += points
        reasons.append("bouncing between apps")

    ratio = float(context.get("baseline_ratio", 0) or 0)
    if ratio >= 2:
        score += min(10, round((ratio - 1) * 4))
        reasons.append("above your usual level for this time")

    if context.get("intent_mismatch"):
        score += 15
        reasons.append("the current surface does not match your stated goal")

    score = min(100, max(0, score))
    label = "intentional" if score < 25 else "warming up" if score < 50 else "drifting" if score < 75 else "deep loop"
    return {"score": score, "label": label, "reasons": reasons[:4]}


def intent_mismatch(profile: dict | None, session: dict | None) -> dict:
    """Explain a likely goal/surface mismatch without reading page content."""
    goal = str((profile or {}).get("mainGoal") or "").strip()
    surfaces = set((session or {}).get("surfaces") or [])
    if not goal or not surfaces.intersection({"shorts", "feed", "explore"}):
        return {"mismatch": False, "reason": ""}
    return {"mismatch": True, "reason": f"You said your main goal is {goal}, but this is an endless-feed surface."}


def adaptive_intervention(outcomes: list[str], default_minutes: int = 10) -> dict:
    """Choose the next nudge from the user's observed outcomes.

    The first few interventions use the normal default. After that, the
    agent prefers the option that has produced the most exits or completed
    rescues, while keeping the recommendation easy to inspect.
    """
    clean = [str(x) for x in outcomes if x]
    if len(clean) < 3:
        return {"action": "rescue", "minutes": default_minutes, "reason": "Not enough personal history yet."}
    exits = sum(x in {"left", "rescued", "rescue_completed"} for x in clean)
    rescue_success = sum(x == "rescue_completed" for x in clean)
    snoozes = clean.count("snoozed")
    if rescue_success >= 2 and rescue_success >= snoozes:
        minutes = 5 if rescue_success / len(clean) >= 0.6 else default_minutes
        return {"action": "rescue", "minutes": minutes,
                "reason": f"Completed Rescue {rescue_success} of the last {len(clean)} interventions."}
    if exits >= 2 and exits > snoozes:
        return {"action": "rescue", "minutes": 5,
                "reason": f"Leaving early worked {exits} times in the last {len(clean)} interventions."}
    return {"action": "rescue", "minutes": default_minutes,
            "reason": f"Your recent interventions produced {exits} exits; trying a short Rescue next."}


def _nice(app: str) -> str:
    return {
        "youtube_shorts": "YouTube Shorts", "instagram_reels": "Instagram Reels", "tiktok": "TikTok",
        "youtube": "YouTube", "instagram": "Instagram", "reddit": "Reddit", "x": "X",
        "facebook": "Facebook", "hackernews": "Hacker News", "twitch": "Twitch",
    }.get(app, app.replace("_", " ").title())


def _mins(seconds: float) -> int:
    return max(1, round(seconds / 60))


def _n(count: float, word: str) -> str:
    """'1 minute', '12 minutes'."""
    count = int(round(count))
    return f"{count} {word}" + ("" if count == 1 else "s")


def detect(sessions: list[dict], now_ms: int, cfg: dict | None = None) -> list[Alert]:
    c = {**DEFAULTS, **(cfg or {})}
    alerts: list[Alert] = []
    live = [s for s in sessions if s["status"] == "open"]

    # 1. Fast-swipe loop
    for s in live:
        if s["app"] not in SHORT_VIDEO_APPS:
            continue
        minutes = s["duration_s"] / 60
        paced = minutes >= c["shorts_min_minutes"] and s["shorts_per_min"] >= c["shorts_min_per_minute"]
        if paced or s["shorts_viewed"] >= c["shorts_hard_count"]:
            gap = 60 / s["shorts_per_min"] if s["shorts_per_min"] else None
            dwell = s["median_short_dwell_s"]
            why = (f"you're giving each one about {_n(max(1, dwell), 'second')} before swiping"
                   if dwell else "you're swiping quickly")
            alerts.append(Alert(
                kind="doom_scroll", app=s["app"], session_id=s["session_id"],
                headline=f"{_n(s['shorts_viewed'], 'video')} in {_n(_mins(s['duration_s']), 'minute')}",
                explanation=(
                    f"That's one every {_n(max(1, gap), 'second')} on {_nice(s['app'])}, and {why}. "
                    "At that pace you're not choosing videos anymore — the swipe is doing the choosing."
                    if gap else
                    f"You've been on {_nice(s['app'])} for {_n(_mins(s['duration_s']), 'minute')} and {why}."
                ),
                stats={k: s[k] for k in ("duration_s", "shorts_viewed", "shorts_per_min", "median_short_dwell_s")}
                      | {"drift_score": drift_score(s)["score"], "drift_label": drift_score(s)["label"]},
            ))

    # 2. Endless feed
    for s in live:
        if s["app"] in SHORT_VIDEO_APPS:
            continue
        if s["duration_s"] / 60 >= c["feed_min_minutes"] and s["scroll_px_per_min"] >= c["feed_min_px_per_minute"]:
            screens = round(s["scroll_px"] / 900)  # roughly one laptop screen height
            alerts.append(Alert(
                kind="feed_scroll", app=s["app"], session_id=s["session_id"],
                headline=f"{_n(_mins(s['duration_s']), 'minute')} of {_nice(s['app'])} feed",
                explanation=(
                    f"You've scrolled past roughly {screens} screens of posts without stopping on one. "
                    "Steady scrolling with no pauses usually means looking for something that isn't coming."
                ),
                stats={k: s[k] for k in ("duration_s", "scroll_px", "scroll_px_per_min")}
                      | {"drift_score": drift_score(s)["score"], "drift_label": drift_score(s)["label"]},
            ))

    # 3. App-hopping
    window_start = now_ms - c["hop_window_minutes"] * 60000
    recent = [s for s in sessions if s["started_at"] >= window_start]
    switches = sum(1 for s in recent if s.get("came_from_app") and s["came_from_app"] != s["app"])
    apps = {s["app"] for s in recent}
    if live and switches >= c["hop_min_switches"] and len(apps) >= c["hop_min_apps"]:
        chain = []
        for s in recent:
            if not chain or chain[-1] != s["app"]:
                chain.append(s["app"])
        shown = " → ".join(_nice(a) for a in chain[-5:])
        alerts.append(Alert(
            kind="app_hopping", app=live[0]["app"], session_id=live[0]["session_id"],
            headline=f"{switches} switches across {len(apps)} apps in {c['hop_window_minutes']} minutes",
            explanation=(
                f"{shown}. Hopping like this is usually restlessness, not interest — "
                "each app gets a few seconds to fix a feeling, then you move on."
            ),
            stats={"switches": switches, "apps": sorted(apps)},
        ))

    # 4. Repeated reopening
    rstart = now_ms - c["reopen_window_minutes"] * 60000
    for s in live:
        reopens = [x for x in sessions
                   if x["app"] == s["app"] and x["started_at"] >= rstart
                   and x.get("reopened_after_s") is not None
                   and x["reopened_after_s"] <= c["reopen_max_gap_seconds"]]
        if len(reopens) >= c["reopen_min_count"]:
            gaps = sorted(x["reopened_after_s"] for x in reopens)
            alerts.append(Alert(
                kind="reopen_loop", app=s["app"], session_id=s["session_id"],
                headline=f"Back on {_nice(s['app'])} {len(reopens)} times in {c['reopen_window_minutes']} minutes",
                explanation=(
                    f"You keep leaving and coming back — once after just {_n(gaps[0], 'second')}. "
                    "When the hand reopens it before you've decided to, it's a habit loop running on its own."
                ),
                stats={"reopens": len(reopens), "shortest_gap_s": gaps[0]},
            ))

    return alerts


def above_baseline(days: dict, today_key: str, hour: int, live_ms: float = 0, *,
                   window_hours: int = 3, min_days: int = 7, factor: float = 2.0,
                   floor_minutes: int = 20) -> dict | None:
    """Personal baseline: is the last few hours unusually heavy *for you*?

    days: the extension's day stats ({"YYYY-MM-DD": {"hours": [24 × ms], ...}}).
    Compares today's distracting time in the window ending at `hour` with the
    median of the same window on previous days. Needs a week of history.
    """
    window = [h for h in range(hour - window_hours + 1, hour + 1) if h >= 0]

    def window_ms(day: dict) -> float:
        hrs = (day or {}).get("hours") or []
        return sum(hrs[h] for h in window if h < len(hrs))

    past = []
    for k in sorted(days):
        if k >= today_key:
            continue
        d = days[k] or {}
        if any((d.get("hours") or [])) or d.get("apps"):
            past.append(window_ms(d))
    past = past[-14:]
    if len(past) < min_days:
        return None
    past.sort()
    mid = len(past) // 2
    typical = past[mid] if len(past) % 2 else (past[mid - 1] + past[mid]) / 2
    today = window_ms(days.get(today_key) or {}) + live_ms
    if today < floor_minutes * 60000 or today < factor * max(typical, 5 * 60000):
        return None
    return {"today_ms": today, "typical_ms": typical, "ratio": round(today / max(typical, 5 * 60000), 1),
            "since_hour": window[0]}


def baseline_alert(b: dict, app: str, session_id: str | None) -> Alert:
    h = b["since_hour"]
    since = f"{(h + 11) % 12 + 1} {'AM' if h < 12 else 'PM'}"
    return Alert(
        kind="above_normal", app=app, session_id=session_id,
        headline=f"{_n(_mins(b['today_ms'] / 1000), 'minute')} on distracting sites since {since}",
        explanation=(
            f"That's about {b['ratio']}× what's normal for you at this time of day. "
            "Tonight is running long — a good moment to stop and pick one thing from your list."
        ),
        stats={"today_ms": round(b["today_ms"]), "typical_ms": round(b["typical_ms"]), "ratio": b["ratio"]},
    )
