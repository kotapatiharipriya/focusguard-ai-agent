"""Smoke tests for the Streamlit dashboard: it renders without errors with the
agent down and with real data. Skipped if Streamlit isn't installed."""

import importlib.util
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = str(ROOT / "app" / "dashboard.py")
sys.path.insert(0, str(Path(__file__).parent))
HAS_ST = importlib.util.find_spec("streamlit") is not None


def texts(at):
    return " ".join(str(m.value) for m in at.markdown)


@unittest.skipUnless(HAS_ST, "streamlit not installed")
class DashboardTests(unittest.TestCase):
    def test_agent_down(self):
        from streamlit.testing.v1 import AppTest
        os.environ["DRIFTGUARD_URL"] = "http://127.0.0.1:9"
        os.environ["DRIFTGUARD_TOKEN"] = "x"
        at = AppTest.from_file(APP, default_timeout=30).run()
        self.assertFalse(at.exception)
        self.assertIn("isn't running", texts(at))

    def test_today_with_data(self):
        from streamlit.testing.v1 import AppTest
        import collector
        import driftguard
        tmp = tempfile.mkdtemp()
        os.environ["DRIFTGUARD_CONFIG"] = os.path.join(tmp, "c.json")
        os.environ["DRIFTGUARD_TOKEN"] = "test-token"
        conn = collector.open_db(Path(tmp) / "d.db")
        engine = driftguard.Engine(conn, driftguard.load_config(), "test-token")
        server = collector.serve(conn=conn, hooks=engine, port=0)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        today = time.strftime("%Y-%m-%d")
        engine.save_ext_state({
            "days": {today: {"apps": {"youtube_shorts": {"ms": 30 * 60000, "visits": 3, "shorts": 90}},
                             "blocked": {"youtube_shorts": 2}, "hours": [0] * 22 + [30 * 60000, 0],
                             "focusMs": 25 * 60000, "pomodoros": 1, "tasksDone": 1, "reasons": {"bored": 2}}},
            "tasks": [{"id": "a", "title": "Write README", "done": False}],
            "rules": [{"id": "youtube_shorts", "label": "YouTube Shorts", "mode": "block", "limitMin": 15}],
            "focus": None, "focusSettings": {"workMin": 25}})
        os.environ["DRIFTGUARD_URL"] = f"http://127.0.0.1:{server.server_address[1]}"
        try:
            at = AppTest.from_file(APP, default_timeout=30).run()
            self.assertFalse(at.exception, at.exception)
            self.assertIn("30 min", texts(at))
            self.assertIn("on distracting sites today", texts(at))
            # Starting a focus session sends a command for the browser
            engine._take_commands()
            at.button[[b.label for b in at.button].index("Start 25-minute focus")].click().run()
            self.assertEqual([c["type"] for c in engine._take_commands()], ["start_focus"])
        finally:
            server.shutdown()
            conn.close()


if __name__ == "__main__":
    unittest.main()
