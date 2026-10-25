"""Tests for the AI agent: the tool-calling loop (against a scripted fake
model server, the way Ollama answers), the guardrails, the rule fallback,
stepping in, planned-session prompts and undoing temporary limits.
Run: python3 -m unittest discover -s agent -p "test_*.py"
"""

import datetime as dt
import json
import os
import sys
import tempfile
import threading
import time
import unittest
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent))
_tmp = tempfile.mkdtemp()
os.environ["DRIFTGUARD_CONFIG"] = os.path.join(_tmp, "config.json")
os.environ["DRIFTGUARD_TOKEN"] = "test-token"

import collector  # noqa: E402
import driftguard  # noqa: E402

MIN = 60000


class FakeModel:
    """Serves /api/chat with a scripted list of assistant messages and records requests."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = []
        model = self

        class H(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                model.requests.append(body)
                if not model.script:
                    msg = {"role": "assistant", "content": "Done."}
                else:
                    msg = model.script.pop(0)
                if msg == "ERROR":
                    self.send_response(500); self.end_headers(); return
                data = json.dumps({"message": msg}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()


def call(name, **args):
    return {"function": {"name": name, "arguments": args}}


def ts_today(h, m=0):
    d = dt.datetime.combine(dt.date.today(), dt.time(h, m))
    return d.timestamp()


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.conn = collector.open_db(Path(self.tmp.name) / "a.db")
        self.addCleanup(self.conn.close)
        cfg = driftguard.load_config()
        cfg["agent"]["daily_plan"] = False
        self.engine = driftguard.Engine(self.conn, cfg, "test-token")
        self.notify = mock.patch.object(driftguard, "notify").start()
        self.addCleanup(mock.patch.stopall)
        yday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        hours = [0] * 24
        hours[22] = 60 * MIN
        days = {(dt.date.today() - dt.timedelta(days=i)).isoformat(): {"apps": {"youtube_shorts": {"ms": 30 * MIN}}, "hours": hours}
                for i in range(2, 8)}
        days[yday] = {"apps": {"reddit": {"ms": 70 * MIN, "visits": 9}, "youtube_shorts": {"ms": 30 * MIN}}, "hours": hours, "blocked": {}}
        self.engine.save_ext_state({
            "days": days,
            "tasks": [{"id": "t1", "title": "Update my resume", "done": False}, {"id": "t2", "title": "Apply to 3 companies", "done": False}],
            "rules": [{"id": "youtube_shorts", "label": "YouTube Shorts", "mode": "block", "limitMin": 15},
                      {"id": "reddit", "label": "Reddit", "mode": "track", "limitMin": 20},
                      {"id": "facebook", "label": "Facebook", "mode": "off", "limitMin": 20}],
            "focus": None, "focusSettings": {"workMin": 25},
            "profile": {"name": "Priya", "mainGoal": "Get an internship offer", "triggers": ["avoiding"]},
        })

    def use_model(self, script):
        fm = FakeModel(script)
        self.addCleanup(fm.close)
        self.engine.cfg["ollama"].update({"enabled": True, "url": fm.url, "model": "test"})
        return fm

    def test_model_plans_through_tools_and_guardrails_hold(self):
        fm = self.use_model([
            {"role": "assistant", "content": "", "tool_calls": [call("get_overview")]},
            {"role": "assistant", "content": "", "tool_calls": [
                call("set_first_task", task="resume"),
                call("schedule_focus", time_hhmm="11:00", minutes=25, task="Update my resume"),
                call("schedule_focus", time_hhmm="08:00", minutes=25),            # already passed: refused
                call("schedule_focus", time_hhmm="21:30", minutes=500, task="Apply to 3 companies"),  # minutes capped
                call("tighten_site", site="Reddit", mode="limit", limit_minutes=15, reason="70 minutes yesterday"),
                call("tighten_site", site="YouTube Shorts", mode="limit", limit_minutes=30),  # looser: refused
                call("tighten_site", site="Facebook", mode="block"),                # user turned it off: refused
            ]},
            {"role": "assistant", "content": "", "tool_calls": [call("finish", message="Start with your resume at 11. Reddit is limited today.")]},
        ])
        self.engine._take_commands()
        plan = self.engine.agent.make_plan(now=ts_today(10, 0))
        self.assertEqual(plan["source"], "ollama")
        self.assertEqual(plan["first_task"], "Update my resume")
        self.assertEqual([(b["time"], b["minutes"]) for b in plan["focus_blocks"]], [("11:00", 25), ("21:30", 90)])
        self.assertEqual([c["site"] for c in plan["site_changes"]], ["reddit"])
        self.assertEqual(plan["message"], "Start with your resume at 11. Reddit is limited today.")
        cmds = self.engine._take_commands()
        self.assertEqual(cmds, [{**cmds[0], "type": "set_site_mode", "args": {"site": "reddit", "mode": "limit", "limit_minutes": 15}}])
        # The model got the tool results, including the refusals, so it can adapt.
        tool_msgs = [m for m in fm.requests[-1]["messages"] if m["role"] == "tool"]
        joined = " ".join(m["content"] for m in tool_msgs)
        self.assertIn("already passed", joined)
        self.assertIn("may only make sites stricter", joined)
        self.assertIn("turned off by the user", joined)
        # get_overview gave it real numbers
        self.assertIn('"Reddit": 70', tool_msgs[0]["content"])
        self.assertEqual(self.engine.plan_summary()["first_task"], "Update my resume")
        log = self.conn.execute("SELECT kind, source FROM agent_log").fetchall()
        self.assertEqual([tuple(r) for r in log], [("plan", "ollama")])

    def test_rules_plan_without_model(self):
        self.engine._take_commands()
        plan = self.engine.agent.make_plan(now=ts_today(10, 0))
        self.assertEqual(plan["source"], "rules")
        self.assertEqual(plan["first_task"], "Update my resume")
        self.assertEqual([b["time"] for b in plan["focus_blocks"]], ["10:30", "21:30"])  # soon + before the 10 PM habit
        self.assertEqual(plan["site_changes"][0]["site"], "reddit")                     # 70 min yesterday, only tracked
        self.assertIn("Start with “Update my resume”", plan["message"])

    def test_model_failure_falls_back_to_rules(self):
        self.use_model(["ERROR"])
        plan = self.engine.agent.make_plan(now=ts_today(10, 0))
        self.assertEqual(plan["source"], "rules")
        self.assertTrue(plan["focus_blocks"])

    def test_temporary_limits_are_undone_next_day(self):
        self.engine.agent.make_plan(now=ts_today(10, 0))
        self.engine._take_commands()
        temp = self.engine._get("temp_changes")
        temp["date"] = (dt.date.today() - dt.timedelta(days=1)).isoformat()
        self.engine._set("temp_changes", temp)
        self.engine.tick()
        cmds = self.engine._take_commands()
        self.assertIn({"site": "reddit", "mode": "track", "limit_minutes": 20}, [c["args"] for c in cmds])

    def test_planned_session_prompts_and_accept(self):
        lt = time.localtime()
        hhmm = f"{lt.tm_hour:02d}:{lt.tm_min:02d}"
        self.engine.save_plan({"date": time.strftime("%Y-%m-%d"), "message": "x", "first_task": "Update my resume",
                               "focus_blocks": [{"id": "b1", "time": hhmm, "minutes": 25, "task": "Update my resume", "status": "planned"}],
                               "site_changes": []})
        self.engine.tick()
        nudges = self.engine._take_nudges()
        self.assertEqual(len(nudges), 1)
        n = nudges[0]
        self.assertEqual((n["action"], n["task"], n["minutes"], n["cta"]), ("focus", "Update my resume", 25, "Start 25 minutes"))
        r = collector.ingest_batch(self.conn, {"events": [{"id": str(uuid.uuid4()), "type": "nudge_response", "app": "driftguard",
                                                           "ts": int(time.time() * 1000), "ref": n["id"], "reason": "accept"}]})
        self.engine.after_ingest(r["_events"])
        self.assertEqual(self.engine._get("plan")["focus_blocks"][0]["status"], "started")

    def test_steps_in_after_repeated_blocked_attempts(self):
        self.use_model([
            {"role": "assistant", "content": "", "tool_calls": [call("get_overview")]},
            {"role": "assistant", "content": "", "tool_calls": [
                call("offer_focus", task="Update my resume", minutes=10, message="Three tries in a few minutes. 10 minutes on your resume?")]},
            {"role": "assistant", "content": "", "tool_calls": [call("finish", message="ok")]},
        ])
        now = int(time.time() * 1000)
        events = [{"id": str(uuid.uuid4()), "type": "blocked", "app": "youtube_shorts", "ts": now - i * 60000, "reason": "block"} for i in range(3)]
        r = collector.ingest_batch(self.conn, {"events": events})
        self.engine.after_ingest(r["_events"])
        for _ in range(50):  # the agent runs in the background
            nudges = self.engine._take_nudges()
            if nudges:
                break
            time.sleep(0.1)
        self.assertEqual(len(nudges), 1)
        self.assertEqual(nudges[0]["kind"], "agent")
        self.assertEqual(nudges[0]["explanation"], "Three tries in a few minutes. 10 minutes on your resume?")
        self.assertEqual((nudges[0]["action"], nudges[0]["minutes"]), ("focus", 10))
        # Cooldown: more attempts right away don't trigger another one
        r = collector.ingest_batch(self.conn, {"events": [{**events[0], "id": str(uuid.uuid4())}]})
        self.engine.after_ingest(r["_events"])
        time.sleep(0.3)
        self.assertEqual(self.engine._take_nudges(), [])

    def test_chat_without_model_understands_start_focus(self):
        self.engine._take_commands()
        r = self.engine.agent.chat("start a 15 min focus on my resume")
        self.assertIn("Started a 15-minute focus session", r["answer"])
        cmds = self.engine._take_commands()
        self.assertEqual(cmds[0]["type"], "start_focus")
        self.assertEqual(cmds[0]["args"]["minutes"], 15)

    def test_chat_with_model_can_act(self):
        self.use_model([
            {"role": "assistant", "content": "", "tool_calls": [call("get_overview")]},
            {"role": "assistant", "content": "", "tool_calls": [call("start_focus_now", minutes=20, task="Apply to 3 companies")]},
            {"role": "assistant", "content": "Started 20 minutes on applications. You've got this."},
        ])
        self.engine._take_commands()
        r = self.engine.agent.chat("help me start on my applications")
        self.assertEqual(r["source"], "ollama")
        self.assertEqual(r["answer"], "Started 20 minutes on applications. You've got this.")
        self.assertEqual(self.engine._take_commands()[0]["args"], {"minutes": 20, "task": "Apply to 3 companies"})


if __name__ == "__main__":
    unittest.main()
