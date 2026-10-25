from __future__ import annotations

from .policy import choose_intervention, score_drift
from .rag import retrieve


def run_demo() -> dict:
    signals = {
        "minutes": 14,
        "short_videos": 47,
        "shorts_per_min": 3.4,
        "scroll_px_per_min": 9200,
        "quick_returns": 4,
        "intent_mismatch": True,
        "goal": "Prepare ML assignment",
    }
    score = score_drift(signals)
    technique = retrieve("fast swiping feed loop study task")
    decision = choose_intervention(score, signals["goal"])
    trace = [
        "Observed coarse browser signals",
        "Calculated a transparent drift score",
        f"Retrieved '{technique['title']}' from the local knowledge base",
        f"Selected a guarded {decision['action']} action",
        "Waiting for human approval before the simulated action",
    ]
    return {"signals": signals, "score": score, "technique": technique, "decision": decision, "trace": trace}
