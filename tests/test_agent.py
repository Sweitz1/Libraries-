import datetime as dt
import functools
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

PAGE = ("<html><head><title>{t}</title></head><body><main><p>The river runs past the old mill. Children learn to "
        "read and write stories there. Farmers bring bread and apples to the market every morning. The library "
        "stays open late in summer.</p>{links}</main></body></html>")


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class AgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: os.environ.get(k) for k in ("LIBRA_HOME", "LIBRA_ALLOW_PRIVATE")}
        os.environ["LIBRA_HOME"] = os.path.join(self.tmp.name, "home")
        os.environ["LIBRA_ALLOW_PRIVATE"] = "1"
        www = os.path.join(self.tmp.name, "www")
        os.makedirs(www)
        for name, links in (("index.html", "<a href='a.html'>A</a><a href='b.html'>B</a><a href='c.html'>C</a>"),
                            ("a.html", ""), ("b.html", ""), ("c.html", "")):
            with open(os.path.join(www, name), "w") as f:
                f.write(PAGE.format(t=name, links=links))
        self.web = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=www))
        self.base = f"http://127.0.0.1:{self.web.server_address[1]}"
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        from libra.skills import default_registry
        from libra.trust import Policy

        self.reg = default_registry(log=None, load_default_dir=False, catalog=None, policy=Policy())

        @self.reg.skill("post_note", "Post a note to the team board", {"text": "what to post"})  # write risk
        def post_note(text: str):
            return "posted"

    def tearDown(self):
        self.web.shutdown()
        self.web.server_close()
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_split_and_plan(self):
        from libra.agent import plan, split_goal

        self.assertEqual(split_goal("scrape https://x.test then summarize it; translate it to Spanish"),
                         ["scrape https://x.test", "summarize it", "translate it to Spanish"])
        self.assertEqual(split_goal("list the links on https://x.test and scrape each of them"),
                         ["list the links on https://x.test", "scrape each of them"])
        p = plan(f"list the links on {self.base}/ then scrape each of them then summarize it in 2 sentences", self.reg)
        self.assertTrue(p.ok, p.problems)
        self.assertEqual([s.skill for s in p.steps], ["links", "scrape", "summarize"])
        self.assertTrue(p.steps[1].foreach)
        self.assertEqual(p.steps[2].args, {"text": "{prev}", "sentences": "2"})
        many = plan("scrape https://a.test and https://b.test", self.reg)
        self.assertEqual(many.steps[0].items, ["https://a.test", "https://b.test"])
        self.assertFalse(plan("book me a flight to Paris", self.reg).ok)  # nothing matches: no guessing
        self.assertIn("summarize what", plan("summarize it", self.reg).problems[0])
        self.assertEqual(plan("post a note saying 'hello team'", self.reg).steps[0].skill, "post_note")

    def test_execute_chain_and_foreach(self):
        from libra.agent import execute, plan

        r = execute(plan(f"list the links on {self.base}/ then scrape each of them then summarize it in 1 sentence",
                         self.reg), self.reg, max_items=2)
        self.assertEqual(r.status, "done", r.problems)
        self.assertEqual(r.steps[1].items, 2)  # limited by max_items
        self.assertEqual(len([s for s in r.output.split(". ") if s]), 1)
        r = execute(plan("what is (2+3)*4", self.reg), self.reg)
        self.assertEqual(r.output, "(2+3)*4 = 20")

    def test_trust_and_limits(self):
        from libra.agent import execute, plan

        p = plan("post a note saying 'hello team'", self.reg)
        r = execute(p, self.reg)  # no one to ask: a write skill is refused
        self.assertEqual(r.status, "blocked")
        self.assertIn("libra trust allow post_note", r.problems[0])
        self.reg.confirm = lambda skill, args: True
        self.assertEqual(execute(p, self.reg).status, "done")
        long = plan("what is 1+1 then what is 2+2 then what is 3+3", self.reg)
        r = execute(long, self.reg, max_steps=2)
        self.assertEqual((r.status, len(r.steps)), ("stopped", 2))
        self.assertEqual(execute(plan("book a flight", self.reg), self.reg).status, "invalid")

    def test_schedules_and_saved_goals(self):
        from libra import agent

        tz = dt.timezone.utc
        at = lambda s: dt.datetime.fromisoformat(s).replace(tzinfo=tz)  # noqa: E731
        self.assertTrue(agent.is_due("daily@07:00", None, at("2026-11-21T08:00")))
        self.assertFalse(agent.is_due("daily@07:00", "2026-11-21T07:00:05+00:00", at("2026-11-21T08:00")))
        self.assertTrue(agent.is_due("daily@07:00", "2026-11-20T07:00:05+00:00", at("2026-11-21T07:01")))
        self.assertFalse(agent.is_due("weekdays@08:30", "2026-11-20T08:31:00+00:00", at("2026-11-22T09:00")))  # Sun
        self.assertTrue(agent.is_due("6h", "2026-11-21T01:00:00+00:00", at("2026-11-21T07:01")))
        self.assertFalse(agent.is_due("30m", "2026-11-21T07:00:00+00:00", at("2026-11-21T07:10")))
        self.assertFalse(agent.is_due(None, None))
        with self.assertRaises(ValueError):
            agent.check_schedule("every tuesday")
        with self.assertRaises(ValueError):
            agent.add_goal("bad", "book me a flight", None, self.reg)  # can't be planned: not saved
        agent.add_goal("sum", "what is 6*7", "hourly", self.reg)
        runs = agent.run_due(self.reg, log=None)
        self.assertEqual([(r.name, r.status, r.output) for r in runs], [("sum", "done", "6*7 = 42")])
        self.assertEqual(agent.run_due(self.reg, log=None), [])  # not due again yet
        self.assertEqual(agent.load_goals()["sum"]["last_status"], "done")
        self.assertEqual(agent.recent_runs(1)[0]["name"], "sum")
        self.assertTrue(agent.remove_goal("sum"))

    def test_server_endpoints_and_pairing(self):
        from libra.server import Engine, make_server

        srv = make_server(Engine(None, None, skills=self.reg, token="tok"), "127.0.0.1", 0)
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        def call(path, body=None):
            req = urllib.request.Request(url + path, json.dumps(body).encode() if body is not None else None,
                                         {"Authorization": "Bearer tok", "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(req, timeout=20) as r:
                    return r.status, json.load(r)
            except urllib.error.HTTPError as e:
                return e.code, json.load(e)

        try:
            status, p = call("/v1/agent/plan", {"goal": "what is 2+2"})
            self.assertTrue(p["ok"])
            self.assertEqual(call("/v1/agent/run", {"goal": "what is 2+2"})[0], 202)
            for _ in range(100):
                state = call("/v1/agent")[1]
                if not state["running"] and state["runs"]:
                    break
                threading.Event().wait(0.05)
            self.assertEqual(state["runs"][0]["output"], "2+2 = 4")
            self.assertEqual(call("/v1/agent/goals", {"name": "x", "goal": "fly to the moon"})[0], 400)
            self.assertEqual(call("/v1/agent/goals", {"name": "x", "goal": "what is 1+1", "every": "daily@07:00"})[0], 200)
            self.assertIn("x", call("/v1/agent")[1]["goals"])
            status, pair = call("/v1/pair?base=" + urllib.request.quote("https://libra.tail1.ts.net"))
            self.assertEqual(pair["url"], "https://libra.tail1.ts.net/#token=tok")
            self.assertTrue(pair["svg"].startswith("<svg"))
            self.assertEqual(call("/v1/pair?base=javascript:alert(1)")[0], 400)
            req = urllib.request.Request(url + "/v1/pair?base=https://x.test")
            with self.assertRaises(urllib.error.HTTPError):  # needs the token: it would reveal it
                urllib.request.urlopen(req, timeout=10)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_summarize(self):
        from libra.skills import extractive_summary

        text = ("Solar panels turn sunlight into electricity. Panels on roofs make electricity for homes. "
                "My cat likes the sunny window. Electricity from solar panels can charge batteries at night.")
        out = extractive_summary(text, 2)
        self.assertEqual(len([s for s in out.split(". ") if s]), 2)
        self.assertNotIn("cat", out)  # the off-topic sentence is left out
        self.assertEqual(extractive_summary("Short text here only.", 3), "Short text here only.")


if __name__ == "__main__":
    unittest.main()
