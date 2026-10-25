"""Tests for the browser collector. Run: python3 -m unittest agent/test_collector.py"""

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
os.environ["DRIFTGUARD_TOKEN"] = "test-token"
import collector  # noqa: E402


def ev(type_, app="youtube_shorts", sid=None, ts=None, **kw):
    return {"id": str(uuid.uuid4()), "type": type_, "app": app, "session_id": sid,
            "ts": ts or int(time.time() * 1000), **kw}


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.conn = collector.open_db(Path(self.tmp.name) / "t.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_accepts_dedupes_and_rejects(self):
        good = ev("short_viewed", sid=str(uuid.uuid4()), dwell_ms=4200)
        bad = [
            ev("unknown_type"),
            ev("scroll_burst", app="YouTube.com"),
            ev("scroll_burst", ts=1),  # wildly wrong clock
            {"type": "scroll_burst"},
            "not an object",
        ]
        r = collector.ingest_batch(self.conn, {"events": [good, *bad]})
        r.pop("_events")
        self.assertEqual(r, {"accepted": 1, "duplicates": 0, "rejected": 5})
        r = collector.ingest_batch(self.conn, {"events": [good]})
        self.assertEqual(r["duplicates"], 1)

    def test_unknown_fields_never_stored(self):
        e = ev("scroll_burst", sid=str(uuid.uuid4()), scroll_px=900, url="https://youtube.com/shorts/abc", title="x")
        collector.ingest_batch(self.conn, {"events": [e]})
        cols = [c[1] for c in self.conn.execute("PRAGMA table_info(browser_events)")]
        self.assertNotIn("url", cols)
        self.assertNotIn("title", cols)

    def test_numbers_are_clamped(self):
        e = ev("scroll_burst", sid=str(uuid.uuid4()), scroll_px=-50, wheel_ticks=10**15)
        collector.ingest_batch(self.conn, {"events": [e]})
        row = self.conn.execute("SELECT scroll_px, wheel_ticks FROM browser_events").fetchone()
        self.assertEqual((row[0], row[1]), (0, collector.MAX_INT))

    def test_batch_limit(self):
        with self.assertRaises(ValueError):
            collector.ingest_batch(self.conn, {"events": [ev("heartbeat")] * (collector.MAX_BATCH + 1)})

    def test_build_sessions(self):
        t0 = int(time.time() * 1000) - 30 * 60000
        a, b = str(uuid.uuid4()), str(uuid.uuid4())
        events = [ev("session_start", sid=a, ts=t0, surface="shorts")]
        for i in range(20):  # 20 shorts in 5 minutes
            events.append(ev("short_viewed", sid=a, ts=t0 + (i + 1) * 15000, dwell_ms=15000))
        events.append(ev("scroll_burst", sid=a, ts=t0 + 60000, scroll_px=4000, wheel_ticks=12))
        events.append(ev("session_end", sid=a, ts=t0 + 5 * 60000, duration_ms=300000, reason="tab_switch"))
        # back to Shorts 90 seconds later, then the browser dies (no end, old heartbeat)
        events.append(ev("session_start", sid=b, ts=t0 + 6.5 * 60000))
        events.append(ev("heartbeat", sid=b, ts=t0 + 7.5 * 60000))
        collector.ingest_batch(self.conn, {"events": events})

        s1, s2 = collector.build_sessions(self.conn)
        self.assertEqual(s1["status"], "closed")
        self.assertEqual(s1["duration_s"], 300)
        self.assertEqual(s1["shorts_viewed"], 20)
        self.assertEqual(s1["shorts_per_min"], 4.0)
        self.assertEqual(s1["median_short_dwell_s"], 15.0)
        self.assertEqual(s1["scroll_px"], 4000)
        self.assertEqual(s2["status"], "stale")
        self.assertEqual(s2["reopened_after_s"], 90)
        self.assertEqual(s2["came_from_app"], "youtube_shorts")


class HttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.server = collector.serve(Path(cls.tmp.name) / "h.db", port=0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.tmp.cleanup()

    def req(self, path, body=None, token="test-token", origin=None):
        headers = {"Content-Type": "application/json", "X-DriftGuard-Token": token}
        if origin:
            headers["Origin"] = origin
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_post_from_extension(self):
        sid = str(uuid.uuid4())
        code, body = self.req("/api/events", {"events": [ev("session_start", sid=sid)]},
                              origin="chrome-extension://abcdef")
        self.assertEqual(code, 200)
        self.assertEqual(body["accepted"], 1)
        code, body = self.req("/api/browser/sessions?hours=1")
        self.assertEqual(code, 200)
        self.assertTrue(any(s["session_id"] == sid for s in body["sessions"]))

    def test_web_page_origin_refused(self):
        code, _ = self.req("/api/events", {"events": [ev("heartbeat")]}, origin="https://evil.example")
        self.assertEqual(code, 403)

    def test_bad_token_refused(self):
        code, _ = self.req("/api/events", {"events": [ev("heartbeat")]}, token="nope")
        self.assertEqual(code, 401)

    def test_health_hides_counts_without_token(self):
        code, body = self.req("/api/health", token="nope")
        self.assertEqual(code, 200)
        self.assertFalse(body["token_ok"])
        self.assertNotIn("events_total", body)


if __name__ == "__main__":
    unittest.main()
