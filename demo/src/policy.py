from __future__ import annotations


def score_drift(signals: dict) -> dict:
    """Transparent policy score; no model is used for arithmetic."""
    score = 0
    reasons = []
    if signals["shorts_per_min"] >= 3:
        score += 25
        reasons.append("fast short-video swiping")
    if signals["minutes"] >= 10:
        score += 20
        reasons.append("long uninterrupted session")
    if signals["scroll_px_per_min"] >= 2500:
        score += 20
        reasons.append("continuous feed scrolling")
    if signals["quick_returns"] >= 3:
        score += 15
        reasons.append("repeated quick returns")
    if signals["intent_mismatch"]:
        score += 15
        reasons.append("activity conflicts with the stated goal")
    score = min(score, 100)
    label = "intentional" if score < 25 else "warming up" if score < 50 else "drifting" if score < 75 else "deep loop"
    return {"score": score, "label": label, "reasons": reasons}


def choose_intervention(score: dict, task: str) -> dict:
    if score["score"] < 60:
        return {"action": "gentle nudge", "minutes": 0, "message": "Pause and choose intentionally before continuing."}
    return {"action": "focus rescue", "minutes": 10, "task": task,
            "message": f"Start a 10-minute Focus Rescue and begin: {task}."}
