"""Memory, learned procedures and the autonomous plan → act → check → adjust loop."""
import functools
import os
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

LONG = ("The river runs past the old mill near the town. Children learn to read and write stories there every day. "
        "Farmers bring bread and apples to the market on Saturdays. The library stays open late during the summer. "
        "Visitors often walk along the river in the evening light.")


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = {k: os.environ.get(k) for k in ("LIBRA_HOME", "LIBRA_ALLOW_PRIVATE")}
        os.environ["LIBRA_HOME"] = os.path.join(self.tmp.name, "home")
        os.environ["LIBRA_ALLOW_PRIVATE"] = "1"

    def tearDown(self):
        for k, v in self.env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()


class MemoryTest(Base):
    def test_add_recall_forget(self):
        from libra.memory import Memory

        m = Memory()
        a = m.add("My Debian server is called atlas and sits in the office", source="cli")
        m.add("my debian server is called atlas and sits in the office")  # near-duplicate: merged
        m.add("I prefer short answers in Spanish")
        self.assertEqual(len(Memory().items), 2)  # persisted
        self.assertEqual(Memory().recall("where is the debian server")[0]["id"], a["id"])
        self.assertEqual(Memory().recall("favourite football team"), [])  # nothing related: nothing returned
        self.assertEqual(len(Memory().forget(a["id"])), 1)
        self.assertEqual(len(Memory().items), 1)

    def test_chat_phrases(self):
        from libra.skills import default_registry

        reg = default_registry(log=None, load_default_dir=False, catalog=None)
        reg.run(*reg.route("remember that the backup runs at 3am"))
        self.assertIn("3am", reg.run(*reg.route("what do you remember about the backup?")).text)
        self.assertIn("Forgot 1", reg.run(*reg.route("forget the backup runs at 3am")).text)


class AutonomyTest(Base):
    def setUp(self):
        super().setUp()
        www = os.path.join(self.tmp.name, "www")
        os.makedirs(www)
        with open(os.path.join(www, "a.html"), "w") as f:
            f.write(f"<html><head><title>A</title></head><body><main><p>{LONG}</p></main></body></html>")
        self.web = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=www))
        self.base = f"http://127.0.0.1:{self.web.server_address[1]}"
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        from libra.skills import default_registry
        from libra.trust import Policy

        self.reg = default_registry(log=None, load_default_dir=False, catalog=None, policy=Policy())
        self.calls = {"flaky": 0}

        @self.reg.skill("headline_finder", "Find the headline of a page", {"url": "page"}, risk="read")
        def headline_finder(url: str):
            raise RuntimeError("this one is broken")

        @self.reg.skill("page_headline", "Get the headline title of a web page", {"url": "page"}, risk="read")
        def page_headline(url: str):
            return "Headline: A"

        @self.reg.skill("weather_report", "Get the weather report for a city", {"city": "which city"}, risk="read")
        def weather_report(city: str = "Paris"):
            self.calls["flaky"] += 1
            if self.calls["flaky"] == 1:
                raise RuntimeError("timed out fetching the forecast")
            return f"Sunny in {city}"

        @self.reg.skill("post_update", "Post an update to the team board", {"text": "what to post"})
        def post_update(text: str):
            return "posted"

    def tearDown(self):
        self.web.shutdown()
        self.web.server_close()
        super().tearDown()

    def test_check_adjust_learn_reuse(self):
        from libra import agent
        from libra.autonomy import pursue
        from libra.memory import Memory

        goal = f"scrape {self.base}/a.html in 2 sentences"
        r = pursue(goal, self.reg, retry_delay=0)
        self.assertEqual(r.status, "done", r.describe())
        self.assertEqual([s.skill for s in r.steps], ["scrape", "summarize"])  # it added the missing step
        self.assertEqual(r.iterations, 2)
        self.assertTrue(any("doesn't meet" in t.thought for t in r.thoughts))
        self.assertEqual(r.learned, "scrape {url1} in {n1} sentences")
        procs = agent.load_procedures()
        self.assertEqual(procs[r.learned]["note"], "repaired")
        self.assertTrue(any("Goal" in it["text"] for it in Memory().items))  # the outcome is remembered

        r2 = pursue(f"scrape {self.base}/a.html in 1 sentences", self.reg, retry_delay=0)
        self.assertEqual(r2.status, "done")
        self.assertEqual(r2.iterations, 1)  # the learned plan worked first time
        self.assertTrue(any("done a goal shaped like this before" in t.thought for t in r2.thoughts))
        self.assertEqual(r2.output.count(". ") + 1, 1)

    def test_alternative_after_failure(self):
        from libra.autonomy import pursue

        r = pursue(f"find the headline of {self.base}/a.html", self.reg, retry_delay=0, remember=False, learn=False)
        self.assertEqual(r.status, "done", r.describe())
        self.assertEqual(r.output, "Headline: A")
        self.assertTrue(any("trying page_headline instead" in t.thought for t in r.thoughts))

    def test_transient_retry_and_blocked(self):
        from libra.autonomy import pursue

        r = pursue("get the weather report for city=Oslo", self.reg, retry_delay=0, remember=False, learn=False)
        self.assertEqual((r.status, r.output), ("done", "Sunny in Oslo"))
        self.assertEqual(self.calls["flaky"], 2)  # one retry after the temporary error
        r = pursue("post an update saying 'deploy finished'", self.reg, retry_delay=0, remember=False)
        self.assertEqual(r.status, "blocked")  # write skill, nobody to approve: it stops instead of working around it
        self.assertTrue(any("won't work around" in (t.criticism or "") for t in r.thoughts))

    def test_impossible_and_limits(self):
        from libra.autonomy import pursue

        r = pursue("book me a flight to Paris", self.reg, retry_delay=0, remember=False)
        self.assertEqual(r.status, "invalid")
        r = pursue("what is 1+1 then what is 2+2 then what is 3+3", self.reg, max_steps=2, remember=False)
        self.assertEqual(r.status, "stopped")

    def test_template(self):
        from libra.agent import _fill, _unfill, template

        shape, values = template("scrape https://x.test/a1 then summarize it in 1 sentence then translate it to Spanish")
        self.assertEqual(shape, "scrape {url1} then summarize it in {n1} sentence then translate it to {lang1}")
        self.assertEqual(_unfill("https://x.test/a1 and 1", values), "{url1} and {n1}")  # never inside a placeholder
        self.assertEqual(_fill("{url1} and {n1}", values), "https://x.test/a1 and 1")


if __name__ == "__main__":
    unittest.main()
