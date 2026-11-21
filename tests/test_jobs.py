"""Durable jobs: a multi-step goal survives a hard kill, resumes without redoing finished work,
never re-runs an interrupted change blindly, pauses for approval across restarts, and ends with
a verifiable result. Also: CORS is off unless an origin is configured."""
import hashlib
import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOAL = "collect the news sources, then digest each of them, then send the report"


def build_registry(state: str, allow_send: bool = False):
    """Three skills that record every call in ``state`` (so a test can see what ran, and how often).
    LIBRA_TEST_SLOW_DIGEST / LIBRA_TEST_SLOW_SEND slow them down so a test can kill mid-step."""
    from libra.skills import default_registry
    from libra.trust import Policy

    policy = Policy({"rules": {"send_report": "allow"}} if allow_send else {}, save=False)
    reg = default_registry(log=None, load_default_dir=False, catalog=None, policy=policy)

    def note(kind, value=""):
        with open(os.path.join(state, kind), "a", encoding="utf-8") as f:
            f.write(value + "\n")

    @reg.skill("collect_sources", "Collect the news sources I follow", risk="read",
               router=lambda m: {} if "collect" in m.lower() else None)
    def collect_sources():
        note("collect")
        items = ["alpha.example/feed", "beta.example/feed", "gamma.example/feed"]
        return "\n".join(items)

    @reg.skill("digest", "Digest one source into a short note", risk="read",
               router=lambda m: {"text": "{prev}"} if "digest" in m.lower() else None)
    def digest(text: str):
        note("digest", text)
        time.sleep(float(os.environ.get("LIBRA_TEST_SLOW_DIGEST", "0")))
        return f"{text}: three new posts about gardening."

    @reg.skill("send_report", "Send the report to me", risk="write",
               router=lambda m: {"body": "{prev}"} if "send the report" in m.lower() else None)
    def send_report(body: str):
        note("send", str(body.count("##")))
        time.sleep(float(os.environ.get("LIBRA_TEST_SLOW_SEND", "0")))
        return f"Report sent with {body.count('##')} digests."

    return reg


def calls(state: str, kind: str) -> list[str]:
    try:
        with open(os.path.join(state, kind), encoding="utf-8") as f:
            return [l.rstrip("\n") for l in f]
    except FileNotFoundError:
        return []


CHILD = """
import os, sys
sys.path.insert(0, {tests!r})
from libra import jobs
import test_jobs
reg = test_jobs.build_registry({state!r}, allow_send={allow!r})
job = jobs.create(test_jobs.GOAL, reg)
print(job["id"], flush=True)
jobs.run(job["id"], reg, retry_delay=0)
"""


class JobsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = os.path.join(self.tmp.name, "home")
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.state)
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = self.home

    def tearDown(self):
        for k in ("LIBRA_TEST_SLOW_DIGEST", "LIBRA_TEST_SLOW_SEND"):
            os.environ.pop(k, None)
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def kill_when(self, ready, allow_send=True, **slow) -> str:
        """Start the goal in a child process and SIGKILL it once ``ready(job)`` is true."""
        from libra import jobs

        env = dict(os.environ, PYTHONPATH=ROOT, **slow)
        script = CHILD.format(tests=os.path.join(ROOT, "tests"), state=self.state, allow=allow_send)
        child = subprocess.Popen([sys.executable, "-c", script], env=env, stdout=subprocess.PIPE, text=True)
        try:
            job_id = child.stdout.readline().strip()
            deadline = time.time() + 30
            while time.time() < deadline:
                try:
                    if ready(jobs.get(job_id)):
                        break
                except (ValueError, jobs.JobError):
                    pass  # mid-write or not created yet
                time.sleep(0.02)
            else:
                self.fail("the child never reached the point to interrupt")
            child.send_signal(signal.SIGKILL)  # no clean-up handlers run: like a power cut
            child.wait(10)
        finally:
            if child.poll() is None:
                child.kill()
            child.stdout.close()
        return job_id

    def test_quoted_text_is_not_split_into_steps(self):
        from libra.agent import split_goal

        self.assertEqual(split_goal('summarize "One. Two. Three." in 2 sentences then translate it to Spanish'),
                         ['summarize "One. Two. Three." in 2 sentences', "translate it to Spanish"])

    def test_plan_has_explicit_dependencies(self):
        from libra import jobs

        job = jobs.create(GOAL, build_registry(self.state))
        self.assertEqual([(s["skill"], s["needs"], s["risk"]) for s in job["steps"]],
                         [("collect_sources", [], "read"), ("digest", [0], "read"), ("send_report", [1], "write")])
        self.assertEqual(job["status"], "queued")
        with self.assertRaises(ValueError):
            jobs.create("frobnicate the quantum flux", build_registry(self.state))

    def test_hard_kill_mid_step_resumes_without_redoing_work(self):
        from libra import jobs

        job_id = self.kill_when(lambda j: len(j["steps"][1]["item_results"]) >= 2, LIBRA_TEST_SLOW_DIGEST="0.3")
        before = jobs.get(job_id)
        self.assertEqual(before["steps"][0]["status"], "done")
        self.assertEqual(before["steps"][1]["status"], "running")  # caught mid-step
        finished = set(before["steps"][1]["item_results"])
        self.assertTrue(jobs.is_running(job_id) is False)  # the lock died with the process
        digested_before = calls(self.state, "digest")

        job = jobs.run(job_id, build_registry(self.state, allow_send=True), retry_delay=0)  # "after the restart"
        self.assertEqual(job["status"], "done")
        self.assertEqual(calls(self.state, "collect"), [""])  # the finished step was not repeated
        redone = calls(self.state, "digest")[len(digested_before):]
        self.assertFalse(set(redone) & finished, "finished items were digested again")
        self.assertEqual(len(job["steps"][1]["item_results"]), 3)
        self.assertEqual(calls(self.state, "send"), ["3"])  # the report has all three, and went once
        res = job["result"]
        self.assertTrue(res["verified"])
        self.assertEqual(res["output"], "Report sent with 3 digests.")
        self.assertEqual(res["sha256"], hashlib.sha256(res["output"].encode()).hexdigest())
        kinds = [e["event"] for e in jobs.events(job_id)]
        self.assertIn("recovered", kinds)
        self.assertIn("resumed", kinds)

    def test_interrupted_change_is_not_rerun_without_approval(self):
        from libra import jobs

        job_id = self.kill_when(lambda j: j["steps"][2]["status"] == "running" and calls(self.state, "send"),
                                LIBRA_TEST_SLOW_SEND="5")
        self.assertEqual(calls(self.state, "send"), ["3"])  # it may have gone out before the kill
        reg = build_registry(self.state, allow_send=True)
        job = jobs.run(job_id, reg, retry_delay=0)
        self.assertEqual(job["status"], "waiting")
        self.assertEqual(job["waiting"]["kind"], "interrupted")
        self.assertIn("may or may not have taken effect", job["waiting"]["reason"])
        self.assertEqual(jobs.resume_all(reg), [])  # the timer won't push it through either
        self.assertEqual(calls(self.state, "send"), ["3"])  # not sent a second time
        jobs.approve(job_id, by="test")
        job = jobs.run(job_id, reg, retry_delay=0)
        self.assertEqual(job["status"], "done")
        self.assertEqual(calls(self.state, "send"), ["3", "3"])  # once more, because the owner said so

        # the other answer: skip it
        job_id = self.kill_when(lambda j: j["steps"][2]["status"] == "running" and len(calls(self.state, "send")) > 2,
                                LIBRA_TEST_SLOW_SEND="5")
        self.assertEqual(jobs.run(job_id, reg)["status"], "waiting")
        jobs.skip(job_id)
        job = jobs.run(job_id, reg)
        self.assertEqual(job["status"], "failed")  # finished, but not every step ran: no verified result
        self.assertIn("skipped by owner", job["result"]["problems"][0])
        self.assertEqual(len(calls(self.state, "send")), 3)

    def test_approval_checkpoint_survives_a_restart_and_is_used_once(self):
        from libra import jobs

        job = jobs.create(GOAL, build_registry(self.state))
        job = jobs.run(job["id"], build_registry(self.state), retry_delay=0)  # send_report "asks"
        self.assertEqual((job["status"], job["waiting"]["kind"], job["waiting"]["step"]), ("waiting", "approval", 2))
        self.assertEqual(calls(self.state, "send"), [])
        job = jobs.run(job["id"], build_registry(self.state))  # a new process: still waiting, nothing sent
        self.assertEqual(job["status"], "waiting")
        self.assertEqual(calls(self.state, "digest").__len__(), 3)  # and nothing redone
        jobs.approve(job["id"], by="device:abc")
        job = jobs.run(job["id"], build_registry(self.state))
        self.assertEqual(job["status"], "done")
        self.assertEqual(job["approvals"][0]["by"], "device:abc")
        self.assertTrue(job["approvals"][0]["used"])
        second = jobs.run(jobs.create(GOAL, build_registry(self.state))["id"], build_registry(self.state))
        self.assertEqual(second["status"], "waiting")  # approvals don't carry over to other jobs
        with self.assertRaises(jobs.JobError):
            jobs.approve(job["id"])  # it isn't waiting

    def test_bounded_retries_failures_and_independent_steps(self):
        from libra import jobs

        reg = build_registry(self.state, allow_send=True)
        flaky = {"n": 0}

        @reg.skill("weather_now", "Check the weather now", risk="read",
                   router=lambda m: {} if "weather" in m.lower() else None)
        def weather_now():
            flaky["n"] += 1
            raise TimeoutError("timed out")

        job = jobs.run(jobs.create("check the weather, then collect the sources", reg)["id"], reg, retry_delay=0)
        self.assertEqual(flaky["n"], 3)  # tried 3 times, then gave up
        self.assertEqual([s["status"] for s in job["steps"]], ["failed", "done"])  # the independent step still ran
        self.assertEqual(job["status"], "failed")
        self.assertEqual(sum(e["event"] == "retry" for e in jobs.events(job["id"])), 2)

        sends = {"n": 0}

        @reg.skill("post_update", "Post the update", risk="write",
                   router=lambda m: {"text": "{prev}"} if "post the update" in m.lower() else None)
        def post_update(text: str):
            sends["n"] += 1
            raise TimeoutError("timed out")

        reg.policy.set("post_update", "allow")
        job = jobs.run(jobs.create("collect the sources, then post the update, then digest each of them", reg)["id"],
                       reg, retry_delay=0)
        self.assertEqual(sends["n"], 1)  # a change is never retried automatically
        self.assertEqual([s["status"] for s in job["steps"]], ["done", "failed", "skipped"])

    def test_cancel_and_time_limit(self):
        from libra import jobs

        reg = build_registry(self.state, allow_send=True)
        job = jobs.create(GOAL, reg, max_seconds=0)
        job = jobs.run(job["id"], reg)
        self.assertEqual((job["status"], job["waiting"]["kind"]), ("waiting", "limit"))
        os.environ["LIBRA_TEST_SLOW_DIGEST"] = "0.3"
        job = jobs.create(GOAL, reg)
        t = threading.Thread(target=jobs.run, args=(job["id"], reg))
        t.start()
        while not calls(self.state, "digest"):
            time.sleep(0.02)
        self.assertTrue(jobs.is_running(job["id"]))
        jobs.cancel(job["id"])
        t.join(10)
        self.assertEqual(jobs.get(job["id"])["status"], "cancelled")
        self.assertEqual(calls(self.state, "send"), [])

    def test_scheduled_goals_resume_and_do_not_pile_up(self):
        from libra import agent, jobs

        reg = build_registry(self.state)
        agent.add_goal("morning", GOAL, "hourly", reg)
        first = agent.run_due(reg, log=None)
        self.assertEqual([r.status for r in first], ["blocked"])  # waiting for approval of the send
        goals = agent.load_goals()
        goals["morning"]["last_run"] = "2000-01-01T00:00:00+00:00"  # due again
        agent.save_goals(goals)
        self.assertEqual(agent.run_due(reg, log=None), [])  # no second copy while the first waits
        self.assertEqual(len(jobs.listing()), 1)
        jobs.approve(jobs.listing()[0]["id"])
        runs = agent.run_due(reg, log=None)  # the timer finishes the approved job, then the goal is due again
        self.assertEqual(runs[0].status, "done")
        self.assertEqual(runs[0].output, "Report sent with 3 digests.")


class JobsServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.state)
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = os.path.join(self.tmp.name, "home")
        from libra.server import Engine, make_server

        self.engine = Engine(None, None, token="main", skills=build_registry(self.state),
                             cors_origins=["https://app.example"])
        self.srv = make_server(self.engine, "127.0.0.1", 0)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        for t in self.engine.job_threads.values():
            t.join(10)
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def req(self, path, body=None, headers=None):
        h = {"Authorization": "Bearer main", "Content-Type": "application/json", **(headers or {})}
        r = urllib.request.Request(self.base + path, json.dumps(body).encode() if body is not None else None, h)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), json.loads(e.read() or b"{}")

    def wait_for(self, job_id, status):
        deadline = time.time() + 20
        while time.time() < deadline:
            job = self.req(f"/v1/jobs/{job_id}")[2]
            if job.get("status") == status and not job.get("running"):
                return job
            time.sleep(0.05)
        self.fail(f"job never became {status}")

    def test_start_approve_finish_over_http(self):
        status, _, out = self.req("/v1/jobs", {"goal": GOAL})
        self.assertEqual(status, 202)
        job_id = out["job"]["id"]
        job = self.wait_for(job_id, "waiting")
        self.assertIn("approval", job["waiting"]["reason"])
        self.assertEqual(self.req("/v1/jobs")[2]["jobs"][0]["id"], job_id)
        self.assertEqual(self.req(f"/v1/jobs/{job_id}/approve", {})[0], 200)
        job = self.wait_for(job_id, "done")
        self.assertTrue(job["result"]["verified"])
        self.assertEqual(job["approvals"][0]["by"], "owner")
        self.assertEqual(self.req(f"/v1/jobs/{job_id}/approve", {})[0], 409)  # not waiting any more
        self.assertEqual(self.req("/v1/jobs/20990101-000000-abcdef")[0], 404)
        self.assertEqual(self.req("/v1/jobs/../../etc")[0], 404)

    def test_cors_only_for_configured_origins(self):
        _, headers, _ = self.req("/v1/info", headers={"Origin": "https://evil.example"})
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        _, headers, _ = self.req("/v1/info", headers={"Origin": "https://app.example"})
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "https://app.example")
        self.assertEqual(headers.get("Vary"), "Origin")


if __name__ == "__main__":
    unittest.main()
