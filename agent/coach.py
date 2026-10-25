"""Weekly coach: turns your last two weeks into a few honest observations
and one concrete change to try.

The observations are computed (deterministic, testable). A local LLM via
Ollama, when enabled, only rewrites them in a warmer voice and never invents
numbers; if it's slow or off, the computed version is returned as is.
"""

from __future__ import annotations

import datetime as dt
import json
import urllib.request


def _fmt(ms: float) -> str:
    m = round(ms / 60000)
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h {m % 60:02d} min"


def _hour(h: int) -> str:
    return f"{(h + 11) % 12 + 1} {'AM' if h < 12 else 'PM'}"


def _distract(day: dict) -> float:
    return sum((a or {}).get("ms", 0) for a in (day.get("apps") or {}).values())


def weekly_insights(days: dict, labels: dict, nudges: dict | None = None,
                    rules: dict | None = None, today: dt.date | None = None,
                    triggers: list[str] | None = None) -> dict:
    """days: {"YYYY-MM-DD": Day} as stored by the extension (last 14 days).
    nudges: {"total": n, "exits": n} from the app's alerts table.
    rules: {app_id: mode} so suggestions fit your current settings."""
    today = today or dt.date.today()
    this_week = [(today - dt.timedelta(days=i)).isoformat() for i in range(7)]
    last_week = [(today - dt.timedelta(days=i)).isoformat() for i in range(7, 14)]
    get = lambda k: days.get(k) or {}
    label = lambda a: labels.get(a) or a.replace("site_", "").replace("_", ".")

    total = sum(_distract(get(k)) for k in this_week)
    prev = sum(_distract(get(k)) for k in last_week)
    focus = sum(get(k).get("focusMs", 0) for k in this_week)
    poms = sum(get(k).get("pomodoros", 0) for k in this_week)
    tasks = sum(get(k).get("tasksDone", 0) for k in this_week)
    passes = sum(get(k).get("passes", 0) for k in this_week)
    reasons: dict[str, int] = {}
    for k in this_week:
        for r, n in (get(k).get("reasons") or {}).items():
            reasons[r] = reasons.get(r, 0) + int(n or 0)

    per_app: dict[str, float] = {}
    blocked: dict[str, int] = {}
    hours = [0.0] * 24
    for k in this_week:
        d = get(k)
        for a, s in (d.get("apps") or {}).items():
            per_app[a] = per_app.get(a, 0) + (s or {}).get("ms", 0)
        for a, n in (d.get("blocked") or {}).items():
            blocked[a] = blocked.get(a, 0) + int(n or 0)
        for h, ms in enumerate((d.get("hours") or [])[:24]):
            hours[h] += ms or 0
    turned = sum(blocked.values())

    insights: list[str] = []
    if total < 60000 and not turned and not focus:
        return {
            "headline": "Not enough data yet",
            "insights": ["Use your browser as usual for a few days and the coach will have something to say."],
            "suggestion": "Add two or three tasks on the Focus page so blocked sites have somewhere useful to send you.",
            "source": "rules",
            "themes": ["focus", "start"],
        }

    if prev > 5 * 60000:
        change = round((total - prev) / prev * 100)
        if change <= -5:
            headline = f"{_fmt(total)} on distracting sites, {abs(change)}% less than last week"
        elif change >= 5:
            headline = f"{_fmt(total)} on distracting sites, {change}% more than last week"
        else:
            headline = f"{_fmt(total)} on distracting sites, about the same as last week"
    else:
        headline = f"{_fmt(total)} on distracting sites this week"

    top = max(per_app.items(), key=lambda x: x[1], default=None)
    if top and top[1] > 5 * 60000 and total:
        insights.append(f"{label(top[0])} took {_fmt(top[1])}, {round(top[1] / total * 100)}% of the total.")

    peak = max(range(24), key=lambda h: hours[h])
    if hours[peak] > 10 * 60000:
        window = sum(hours[(peak + o) % 24] for o in (-1, 0, 1))
        insights.append(f"Your drift clusters around {_hour(peak)}: {_fmt(window)} this week in the hours around it.")

    if turned:
        most = max(blocked.items(), key=lambda x: x[1])
        insights.append(f"FocusGuard AI turned you around {turned} time{'s' if turned != 1 else ''}, most often from {label(most[0])} ({most[1]}).")

    if focus:
        insights.append(f"You focused for {_fmt(focus)} across {poms} finished session{'s' if poms != 1 else ''} "
                        f"and got {tasks} task{'s' if tasks != 1 else ''} done.")
    else:
        insights.append("No focus sessions yet this week.")

    reason_words = {"bored": "bored", "avoiding": "avoiding something", "tired": "tired", "habit": "on autopilot", "stress": "stressed"}
    if sum(reasons.values()) >= 3:
        top_r = max(reasons.items(), key=lambda x: x[1])
        insights.append(f"When FocusGuard AI asked why, you said you were {reason_words.get(top_r[0], top_r[0])} "
                        f"most often ({top_r[1]} of {sum(reasons.values())} times).")
    else:
        top_r = None

    if nudges and nudges.get("total"):
        insights.append(f"After {nudges['total']} nudges, you stepped away {nudges['exits']} times.")

    # One suggestion, the most useful first.
    rules = rules or {}
    suggestion = None
    if top and top[1] > 60 * 60000 and rules.get(top[0]) in ("track", None):
        suggestion = f"Try setting {label(top[0])} to Limit, 15 minutes a day. It's your biggest time sink and it's not limited yet."
    elif hours[peak] > 30 * 60000 and peak >= 20:
        suggestion = f"Late evenings are your weak spot. Start one focus session around {_hour(max(peak - 1, 0))} before the scrolling usually begins."
    elif passes >= 5:
        suggestion = f"You used {passes} passes this week. If most weren't really needed, turn on strict mode."
    elif not focus:
        suggestion = "Run one 25-minute focus session a day this week, even on a small task. It's the habit that makes the blocking stick."
    elif turned >= 15:
        suggestion = "You keep reaching for blocked sites. Before opening the browser, write the one task you're there for at the top of your list."
    else:
        suggestion = "Keep the streak: one focus session before you open any feed tomorrow."

    # Themes steer retrieval of a matching technique from the knowledge base.
    themes: list[str] = []
    theme_for = {"bored": "bored boredom", "avoiding": "avoiding procrastination start stuck",
                 "tired": "tired exhausted rest", "habit": "habit autopilot friction",
                 "stress": "stress anxiety overwhelmed", "fomo": "fomo missing out news checking"}
    if top_r:
        themes.append(theme_for.get(top_r[0], top_r[0]))
    elif triggers:
        # No in-the-moment answers yet: use what you told FocusGuard AI during setup.
        themes.append(theme_for.get(triggers[0], triggers[0]))
    if hours[peak] > 10 * 60000 and (peak >= 20 or peak <= 2):
        themes.append("late night evening sleep bed")
    if top and top[0] in ("youtube_shorts", "instagram_reels", "tiktok"):
        themes.append("shorts reels swiping infinite scroll")
    if turned >= 15:
        themes.append("urge reopening checking")
    if not focus:
        themes.append("focus pomodoro start")
    if passes >= 5:
        themes.append("friction too easy")
    if not themes:
        themes.append("habit trigger routine")

    return {"headline": headline, "insights": insights, "suggestion": suggestion, "source": "rules", "themes": themes}


def rewrite_with_llm(result: dict, ollama: dict, context: dict | None = None) -> dict:
    """Optional: let a local model rephrase, grounded in retrieved context
    (a technique from the knowledge base and the user's own goals).
    Numbers and facts stay the same; retrieval results are passed through untouched."""
    if not ollama.get("enabled"):
        return result
    core = {k: result[k] for k in ("headline", "insights", "suggestion")}
    prompt = (
        "You are FocusGuard AI, a warm, direct coach helping someone stop doom-scrolling. "
        "Rewrite the following weekly summary. Keep every number exactly as given. "
        "If the person's goals are given, connect the suggestion to them in their own terms. "
        "If a technique is given, base the suggestion on it. Never invent facts.\n"
        "Return JSON with keys headline (one sentence), insights (list of at most 4 short sentences), "
        "suggestion (one or two sentences). No emojis, no lecturing.\n\n"
        f"Summary: {json.dumps(core)}\n"
        f"Context: {json.dumps(context or {})}"
    )
    try:
        req = urllib.request.Request(
            ollama["url"].rstrip("/") + "/api/generate",
            data=json.dumps({"model": ollama["model"], "prompt": prompt, "stream": False, "format": "json"}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=max(ollama.get("timeout_s", 4), 10)) as r:
            out = json.loads(json.loads(r.read()).get("response", "{}"))
        if isinstance(out.get("insights"), list) and out.get("headline") and out.get("suggestion"):
            return {**result, "headline": str(out["headline"])[:200], "insights": [str(i)[:240] for i in out["insights"][:4]],
                    "suggestion": str(out["suggestion"])[:300], "source": "ollama"}
    except Exception:
        pass
    return result
