from __future__ import annotations


KNOWLEDGE = [
    {"title": "Stopping cues", "terms": {"swiping", "fast", "loop", "feed"},
     "text": "Add a clear stopping cue: put the phone down, name the next task, and start for two minutes."},
    {"title": "Five-minute start", "terms": {"avoid", "task", "start", "study"},
     "text": "Make the first step tiny. Open the file, write one sentence, or solve one problem before deciding what comes next."},
]


def retrieve(query: str) -> dict:
    words = set(query.lower().split())
    ranked = sorted(KNOWLEDGE, key=lambda item: len(words & item["terms"]), reverse=True)
    return ranked[0]
