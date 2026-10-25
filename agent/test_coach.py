"""Tests for the weekly coach. Run: python3 -m unittest discover -s agent -p "test_*.py" """

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import coach  # noqa: E402

MIN = 60000
TODAY = dt.date(2026, 10, 18)


def day(apps=None, blocked=None, hours=None, focus=0, poms=0, tasks=0, passes=0):
    h = [0] * 24
    for k, v in (hours or {}).items():
        h[k] = v
    return {"apps": {a: {"ms": ms, "visits": 3, "shorts": 0} for a, ms in (apps or {}).items()},
            "blocked": blocked or {}, "hours": h, "focusMs": focus, "pomodoros": poms,
            "tasksDone": tasks, "passes": passes}


def key(i):
    return (TODAY - dt.timedelta(days=i)).isoformat()


class CoachTests(unittest.TestCase):
    labels = {"youtube_shorts": "YouTube Shorts", "reddit": "Reddit"}

    def test_empty(self):
        r = coach.weekly_insights({}, self.labels, today=TODAY)
        self.assertEqual(r["headline"], "Not enough data yet")

    def test_week_over_week_and_peak_hour(self):
        days = {}
        for i in range(7):  # this week: 20 min/day of Reddit, mostly at 10pm
            days[key(i)] = day({"reddit": 20 * MIN}, {"youtube_shorts": 2}, {22: 20 * MIN}, focus=25 * MIN, poms=1, tasks=1)
        for i in range(7, 14):  # last week: 40 min/day
            days[key(i)] = day({"reddit": 40 * MIN})
        r = coach.weekly_insights(days, self.labels, {"total": 4, "exits": 3}, {"reddit": "track"}, today=TODAY)
        self.assertIn("50% less than last week", r["headline"])
        text = " ".join(r["insights"])
        self.assertIn("Reddit took 2 h 20 min, 100%", text)
        self.assertIn("around 10 PM", text)
        self.assertIn("turned you around 14 times, most often from YouTube Shorts", text)
        self.assertIn("2 h 55 min across 7 finished sessions", text)
        self.assertIn("After 4 nudges, you stepped away 3 times", text)
        self.assertIn("Limit", r["suggestion"])  # biggest sink, still only tracked

    def test_late_night_suggestion_when_already_limited(self):
        days = {key(i): day({"reddit": 70 * MIN}, hours={23: 70 * MIN}, focus=MIN) for i in range(7)}
        r = coach.weekly_insights(days, self.labels, rules={"reddit": "limit"}, today=TODAY)
        self.assertIn("Late evenings", r["suggestion"])

    def test_llm_disabled_returns_rules(self):
        base = {"headline": "h", "insights": ["a"], "suggestion": "s", "source": "rules"}
        self.assertIs(coach.rewrite_with_llm(base, {"enabled": False}), base)


if __name__ == "__main__":
    unittest.main()
