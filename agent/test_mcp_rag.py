"""Tests for RAG retrieval, the personal baseline rule, the state hub and the
MCP server (spoken to over real stdio, the way Claude would).
Run: python3 -m unittest discover -s agent -p "test_*.py"
"""

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
_cfg_dir = tempfile.mkdtemp()
os.environ["DRIFTGUARD_CONFIG"] = os.path.join(_cfg_dir, "config.json")
os.environ["DRIFTGUARD_TOKEN"] = "test-token"

import collector  # noqa: E402
import detector  # noqa: E402
import driftguard  # noqa: E402
import rag  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MIN = 60000


def days_with_evenings(n=10, evening_min=20):
    out = {}
    for i in range(1, n + 1):
        k = time.strftime("%Y-%m-%d", time.localtime(time.time() - i * 86400))
        hours = [0] * 24
        hours[21] = evening_min * MIN
        out[k] = {"apps": {"youtube_shorts": {"ms": evening_min * MIN, "visits": 3, "shorts": 40}}, "blocked": {"youtube_shorts": 2},
                  "hours": hours, "focusMs": 25 * MIN, "pomodoros": 1, "tasksDone": 1, "passes": 0,
                  "reasons": {"avoiding": 2, "bored": 1}}
    return out


class RagTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.kb = rag.Knowledge(ROOT)

    def test_finds_matching_technique(self):
        self.assertEqual(self.kb.search("bored nothing to do", k=1, kinds=["techniques"])[0]["title"], "Keep a ready list for boredom")
        self.assertEqual(self.kb.search("late night bed sleep", k=1, kinds=["techniques"])[0]["title"], "An evening wind-down that isn't a feed")
        self.assertEqual(self.kb.search("avoiding procrastination start stuck", k=1, kinds=["techniques"])[0]["title"], "The five-minute start")

    def test_empty_goal_template_is_not_indexed(self):
        self.assertEqual(self.kb.search("goal matters instead", kinds=["me"]), [])

    def test_your_own_notes_are_found(self):
        with tempfile.TemporaryDirectory() as d:
            me = Path(d, "knowledge", "me")
            me.mkdir(parents=True)
            (me / "goals.md").write_text("# My goals\n\n## What matters most right now\nInternship interviews in November. LeetCode daily.\n")
            kb = rag.Knowledge(Path(d))
            hit = kb.search("goal matters interviews", kinds=["me"])[0]
            self.assertIn("Internship interviews", hit["text"])
            (me / "goals.md").write_text("# My goals\n\n## Why\nSleep before midnight.\n")  # edits are picked up
            time.sleep(0.01)
            self.assertIn("midnight", kb.search("sleep why", kinds=["me"])[0]["text"])


class BaselineTests(unittest.TestCase):
    def test_flags_unusual_evening_only_with_history(self):
        days = days_with_evenings(10, 20)
        today = time.strftime("%Y-%m-%d")
        days[today] = {"hours": [0] * 24}
        days[today]["hours"][20] = 30 * MIN
        b = detector.above_baseline(days, today, 21, live_ms=25 * MIN)
        self.assertIsNotNone(b)
        self.assertEqual(b["ratio"], 2.8)
        a = detector.baseline_alert(b, "youtube_shorts", None)
        self.assertIn("2.8× what's normal for you", a.explanation)
        # A normal evening isn't flagged
        days[today]["hours"][20] = 15 * MIN
        self.assertIsNone(detector.above_baseline(days, today, 21, live_ms=0))
        # Too little history: no baseline yet
        few = dict(list(days_with_evenings(3, 20).items()))
        self.assertIsNone(detector.above_baseline(few, today, 21, live_ms=90 * MIN))


class HubAndMcpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.conn = collector.open_db(Path(cls.tmp.name) / "h.db")
        cls.engine = driftguard.Engine(cls.conn, driftguard.load_config(), "test-token")
        cls.server = collector.serve(conn=cls.conn, hooks=cls.engine, port=0)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        today = time.strftime("%Y-%m-%d")
        days = days_with_evenings(8)
        days[today] = {"apps": {"instagram": {"ms": 12 * MIN, "visits": 4, "shorts": 0}}, "blocked": {"youtube_shorts": 3},
                       "hours": [0] * 24, "focusMs": 50 * MIN, "pomodoros": 2, "tasksDone": 1, "passes": 0}
        cls.engine.save_ext_state({
            "days": days,
            "tasks": [{"id": "t1", "title": "Finish resume", "done": False, "pomodoros": 2},
                      {"id": "t2", "title": "Read OS chapter 4", "done": False}],
            "rules": [{"id": "youtube_shorts", "label": "YouTube Shorts", "mode": "block"},
                      {"id": "instagram", "label": "Instagram", "mode": "limit"}],
            "focus": None, "focusSettings": {"workMin": 25},
        })

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.conn.close()
        cls.tmp.cleanup()

    def mcp(self, *messages):
        env = {**os.environ, "DRIFTGUARD_URL": self.url, "DRIFTGUARD_TOKEN": "test-token"}
        proc = subprocess.run([sys.executable, str(ROOT / "agent" / "mcp_server.py")],
                              input="\n".join(json.dumps(m) for m in messages) + "\n",
                              capture_output=True, text=True, env=env, timeout=30)
        return [json.loads(l) for l in proc.stdout.splitlines() if l.strip()]

    def call(self, name, args=None, i=2):
        out = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}},
                       {"jsonrpc": "2.0", "method": "notifications/initialized"},
                       {"jsonrpc": "2.0", "id": i, "method": "tools/call", "params": {"name": name, "arguments": args or {}}})
        res = out[-1]["result"]
        return res["content"][0]["text"], res["isError"]

    def test_handshake_and_tool_list(self):
        out = self.mcp({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
                       {"jsonrpc": "2.0", "method": "notifications/initialized"},
                       {"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        self.assertEqual(len(out), 2)  # no reply to the notification
        self.assertEqual(out[0]["result"]["protocolVersion"], "2025-06-18")
        names = {t["name"] for t in out[1]["result"]["tools"]}
        self.assertTrue({"get_today", "get_report", "start_focus", "add_task", "set_site_mode", "search_notes", "break_down_task"} <= names)

    def test_today_and_report(self):
        text, err = self.call("get_today")
        self.assertFalse(err)
        self.assertIn("12 min on distracting sites", text)
        self.assertIn("turned around 3 times", text)
        text, err = self.call("get_report", {"days": 7})
        self.assertIn("YouTube Shorts: 2 h 00 min", text)

    def test_actions_queue_commands_for_the_browser(self):
        self.engine._take_commands()
        _, err = self.call("add_task", {"title": "Apply to 3 internships"})
        self.assertFalse(err)
        _, err = self.call("start_focus", {"minutes": 25, "task": "Finish resume"})
        _, err = self.call("set_site_mode", {"site": "netflix.com", "mode": "limit", "limit_minutes": 30})
        text, err = self.call("complete_task", {"task": "OS chapter"})
        self.assertIn("Read OS chapter 4", text)
        cmds = self.engine._take_commands()
        self.assertEqual([c["type"] for c in cmds], ["add_task", "start_focus", "set_site_mode", "complete_task"])
        self.assertEqual(cmds[2]["args"], {"site": "netflix.com", "mode": "limit", "limit_minutes": 30})
        self.assertEqual(cmds[3]["args"], {"id": "t2"})

    def test_grounded_coaching(self):
        text, err = self.call("get_weekly_coaching")
        self.assertFalse(err)
        self.assertIn("avoiding something", text)
        # The technique is retrieved from the knowledge base and fits the suggestion (late evenings).
        self.assertIn("Technique: An evening wind-down", text)
        self.assertIn("knowledge/techniques/evening-wind-down.md", text)
        self.assertTrue(any(self.engine.history_dir.glob("*-W*.md")))  # coaching is kept for next time

    def test_search_notes_and_breakdown(self):
        text, _ = self.call("search_notes", {"query": "keep reopening the app"})
        self.assertIn("Urge surfing", text)
        text, _ = self.call("break_down_task", {"task": "Write my resume"})
        self.assertIn("1.", text)

    def test_onboarding_answers_reach_the_coach(self):
        prof = {"name": "Priya", "mainGoal": "Get an internship offer by March", "goals": ["career"],
                "sites": ["youtube_shorts"], "times": ["late"], "triggers": ["stress"], "completedAt": 1}
        st = self.engine._get("ext_state")
        self.engine.save_ext_state({**st, "profile": prof})
        md = (self.engine.profile_dir / "profile.md").read_text()
        self.assertIn("My main goal: Get an internship offer by March.", md)
        self.assertIn("What pulls me in: stress or anxiety.", md)
        hit = self.engine.kb.search("goal matters internship", kinds=["me"])[0]
        self.assertIn("internship", hit["text"])
        self.engine.save_ext_state(st)  # restore for other tests

    def test_app_not_running(self):
        env = {**os.environ, "DRIFTGUARD_URL": "http://127.0.0.1:9", "DRIFTGUARD_TOKEN": "x"}
        proc = subprocess.run([sys.executable, str(ROOT / "agent" / "mcp_server.py")],
                              input=json.dumps({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "get_today", "arguments": {}}}) + "\n",
                              capture_output=True, text=True, env=env, timeout=30)
        res = json.loads(proc.stdout)["result"]
        self.assertTrue(res["isError"])
        self.assertIn("isn't running", res["content"][0]["text"])


if __name__ == "__main__":
    unittest.main()
