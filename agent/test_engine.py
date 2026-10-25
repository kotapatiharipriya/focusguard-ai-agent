"""Tests for detection rules and the always-on engine.
Run: python3 -m unittest discover -s agent -p "test_*.py"
"""

import os
import sys
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
tmp_cfg = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
os.unlink(tmp_cfg.name)
os.environ["DRIFTGUARD_CONFIG"] = tmp_cfg.name
os.environ["DRIFTGUARD_TOKEN"] = "test-token"

import collector  # noqa: E402
import detector  # noqa: E402
import driftguard  # noqa: E402

MIN = 60000


def ev(type_, app, sid, ts, **kw):
    return {"id": str(uuid.uuid4()), "type": type_, "app": app, "session_id": sid, "ts": int(ts), **kw}


def shorts_loop(t_end, minutes=10, per_min=4, sid=None):
    sid = sid or str(uuid.uuid4())
    t0 = t_end - minutes * MIN
    out = [ev("session_start", "youtube_shorts", sid, t0, surface="shorts")]
    n = int(minutes * per_min)
    for i in range(n):
        out.append(ev("short_viewed", "youtube_shorts", sid, t0 + (i + 1) * (minutes * MIN / n), dwell_ms=60000 // per_min))
    return out, sid


def session(app, start, end, sid=None, closed=True):
    sid = sid or str(uuid.uuid4())
    out = [ev("session_start", app, sid, start)]
    if closed:
        out.append(ev("session_end", app, sid, end, duration_ms=end - start, reason="tab_switch"))
    else:
        out.append(ev("heartbeat", app, sid, end))
    return out


class DetectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = collector.open_db(Path(self.tmp.name) / "d.db")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)
        self.now = int(time.time() * 1000)

    def run_rules(self, events):
        collector.ingest_batch(self.conn, {"events": events})
        return detector.detect(collector.build_sessions(self.conn, now_ms=self.now), self.now)

    def test_fast_swipe_loop(self):
        events, _ = shorts_loop(self.now, minutes=10, per_min=4)
        (a,) = self.run_rules(events)
        self.assertEqual(a.kind, "doom_scroll")
        self.assertIn("40 videos in 10 minutes", a.headline)
        self.assertIn("one every 15 seconds", a.explanation)

    def test_slow_watching_is_not_a_loop(self):
        events, _ = shorts_loop(self.now, minutes=10, per_min=1)
        self.assertEqual(self.run_rules(events), [])

    def test_app_hopping(self):
        apps = ["reddit", "x", "youtube", "reddit", "instagram", "x"]
        events, t = [], self.now - 9 * MIN
        for i, app in enumerate(apps):
            last = i == len(apps) - 1
            events += session(app, t, t + 80000, closed=not last)
            t += 90000
        kinds = {a.kind for a in self.run_rules(events)}
        self.assertIn("app_hopping", kinds)

    def test_reopen_loop(self):
        events, t = [], self.now - 20 * MIN
        for i in range(4):
            last = i == 3
            events += session("reddit", t, self.now if last else t + 2 * MIN, closed=not last)
            t += 2 * MIN + 60000  # back after one minute
        kinds = {a.kind for a in self.run_rules(events)}
        self.assertIn("reopen_loop", kinds)


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = collector.open_db(Path(self.tmp.name) / "e.db")
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.conn.close)
        self.engine = driftguard.Engine(self.conn, driftguard.load_config(), "test-token")
        self.notify = mock.patch.object(driftguard, "notify").start()
        self.addCleanup(mock.patch.stopall)

    def post(self, events):
        r = collector.ingest_batch(self.conn, {"events": events})
        return self.engine.after_ingest(r["_events"])

    def test_nudge_rescue_recover(self):
        now = int(time.time() * 1000)
        events, sid = shorts_loop(now)
        r = self.post(events)
        (nudge,) = r["nudges"]
        self.assertEqual(nudge["kind"], "doom_scroll")
        # Delivered once only, and the same loop doesn't re-nudge during cooldown
        self.assertEqual(self.post([ev("heartbeat", "youtube_shorts", sid, now)])["nudges"], [])
        self.engine.tick()
        self.assertEqual(self.post([ev("scroll_burst", "youtube_shorts", sid, now, scroll_px=10)])["nudges"], [])

        # You pick Focus Rescue in the card
        r = self.post([
            ev("nudge_response", "driftguard", None, now, ref=nudge["id"], reason="rescue"),
            ev("focus_rescue_start", "driftguard", None, now, ref=nudge["id"], duration_ms=10 * MIN),
        ])
        self.assertGreater(r["control"]["rescue_until"], now + 9 * MIN)
        st = self.engine.status()
        self.assertEqual(st["alerts"][0]["outcome"], "rescued")

        # ...and finish it
        r = self.post([ev("focus_rescue_end", "driftguard", None, now, reason="completed_better")])
        self.assertEqual(r["control"]["rescue_until"], 0)
        self.assertEqual(self.engine.status()["alerts"][0]["outcome"], "rescue_completed")
        self.assertEqual(self.engine.status()["today"]["rescues_completed"], 1)

    def test_snooze_then_firmer_nudge(self):
        now = int(time.time() * 1000)
        events, sid = shorts_loop(now)
        (nudge,) = self.post(events)["nudges"]
        self.post([ev("nudge_response", "driftguard", None, now, ref=nudge["id"], reason="snooze")])
        # pretend the snooze was 6 minutes ago
        with self.conn:
            self.conn.execute("UPDATE alerts SET outcome_at=? WHERE id=?", (now - 6 * MIN, nudge["id"]))
        (again,) = self.post([ev("short_viewed", "youtube_shorts", sid, now, dwell_ms=5000)])["nudges"]
        self.assertEqual(again["level"], 2)
        self.assertIn("five more minutes", again["explanation"])

    def test_native_fallback_when_page_card_not_picked_up(self):
        now = int(time.time() * 1000)
        events, _ = shorts_loop(now)
        collector.ingest_batch(self.conn, {"events": events})
        self.engine.tick()  # alert created, but no POST picks it up
        with self.conn:
            self.conn.execute("UPDATE alerts SET created_at=?", (now - 30000,))
        self.engine.tick()
        self.notify.assert_called_once()

    def test_paused_means_silent(self):
        self.engine.pause(60)
        now = int(time.time() * 1000)
        events, _ = shorts_loop(now)
        self.assertEqual(self.post(events)["nudges"], [])

    def test_outcome_left(self):
        now = int(time.time() * 1000)
        events, sid = shorts_loop(now)
        (nudge,) = self.post(events)["nudges"]
        self.post([ev("session_end", "youtube_shorts", sid, now + 1000, reason="tab_switch")])
        with self.conn:
            self.conn.execute("UPDATE alerts SET created_at=? WHERE id=?", (now - 4 * MIN, nudge["id"]))
        self.engine.tick()
        self.assertEqual(self.engine.status()["alerts"][0]["outcome"], "left")

    def test_no_nudges_during_focus_session(self):
        now = int(time.time() * 1000)
        self.post([ev("focus_start", "driftguard", None, now, duration_ms=25 * MIN, reason="pomodoro")])
        events, _ = shorts_loop(now)
        self.assertEqual(self.post(events)["nudges"], [])
        self.post([ev("focus_end", "driftguard", None, now, reason="completed")])
        self.engine.tick()
        self.assertEqual(len(self.engine.status()["alerts"]), 1)

    def test_coach_endpoint(self):
        code, body = self.engine.handle_post("/api/coach", {"days": {}, "labels": {}})
        self.assertEqual(code, 200)
        self.assertIn("headline", body)

    def test_status_page_renders(self):
        html = self.engine.page("/")
        self.assertIn("FocusGuard AI", html)
        self.assertIn("Start Focus Rescue", html)


if __name__ == "__main__":
    unittest.main()
