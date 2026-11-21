"""Fixes from the security review: trust precedence, redaction, parallel generation with limits,
connection caps and deadlines, rate limits, input caps, per-device tokens, and trust on every route."""
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from unittest import mock


class TrustAndRedactionTest(unittest.TestCase):
    def test_deny_wins_over_a_more_specific_allow(self):
        from libra.trust import Policy

        p = Policy({"rules": {"mcp.github": "deny", "mcp.github.list_issues": "allow"}}, save=False)
        self.assertEqual(p.decide("mcp.github.list_issues", "read")[0], "deny")
        p = Policy({"rules": {"mcp.github.delete_repo": "deny", "mcp.github": "allow"}}, save=False)
        self.assertEqual(p.decide("mcp.github.delete_repo", "danger")[0], "deny")
        self.assertEqual(p.decide("mcp.github.list_issues", "read")[0], "allow")
        p = Policy({"rules": {"mcp": "ask", "mcp.files": "allow"}}, save=False)  # no deny: most specific decides
        self.assertEqual(p.decide("mcp.files.read", "write"), ("allow", "rule for mcp.files"))
        p = Policy({"rules": {"api.bank.transfer": "allow"}, "defaults": {"danger": "deny"}}, save=False)
        self.assertEqual(p.decide("api.bank.transfer", "danger")[0], "allow")  # an explicit rule beats a default

    def test_recursive_redaction(self):
        from libra.trust import redact

        out = redact({"q": "weather", "nested": {"deeper": {"api_key": "k1", "list": [{"password": "p"}, "x"]}},
                      "headers": {"Authorization": "Bearer abcdefghijkl"},
                      "url": "https://x.test/a?token=SECRET123&page=2", "note": "my key is sk-ABCDEFGHIJKLMN",
                      "discord": "Bot MTIzNDU2Nzg5.abc.defghij", "tuple": ("ghp_ABCDEFGHIJKLMNOP",)})
        text = json.dumps(out)
        for secret in ("k1", '"p"', "abcdefghijkl", "SECRET123", "sk-ABCDEFGHIJKLMN", "MTIzNDU2Nzg5", "ghp_ABCD"):
            self.assertNotIn(secret, text)
        self.assertEqual(out["q"], "weather")
        self.assertIn("page=2", out["url"])


class Slow:
    """A stand-in for text generation that takes ~0.6 s per reply."""

    def __init__(self):
        self.running = 0
        self.peak = 0
        self.lock = threading.Lock()

    def __call__(self, model, tokenizer, prompt, params, stop_tokens):
        with self.lock:
            self.running += 1
            self.peak = max(self.peak, self.running)
        try:
            for word in ("one ", "two ", "three"):
                time.sleep(0.2)
                yield word
        finally:
            with self.lock:
                self.running -= 1


class FakeModel:
    def num_parameters(self):
        return 1


class FakeTok:
    def encode(self, text):
        return list(range(len(text.split())))


class ServerHardeningTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = self.tmp.name

    def tearDown(self):
        for s in getattr(self, "servers", []):
            s.shutdown()
            s.server_close()
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def start(self, **engine_args):
        from libra.server import Engine, make_server

        limits = engine_args.pop("limits", {})
        engine = Engine(FakeModel(), FakeTok(), token="main-token", **engine_args)
        srv = make_server(engine, "127.0.0.1", 0, **limits)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers = getattr(self, "servers", []) + [srv]
        return f"http://127.0.0.1:{srv.server_address[1]}", engine

    def post(self, base, path, body, token="main-token"):
        req = urllib.request.Request(base + path, json.dumps(body).encode(),
                                     {"Content-Type": "application/json", "Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, dict(r.headers), r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, dict(e.headers), e.read().decode()

    def get(self, base, path, token="main-token"):
        req = urllib.request.Request(base + path, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read() or b"{}")

    def chat(self, base):
        return self.post(base, "/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}],
                                                        "max_tokens": 5})

    def test_generations_run_in_parallel(self):
        slow = Slow()
        with mock.patch("libra.server.stream_text", slow):
            base, _ = self.start(max_generations=2)
            t0 = time.time()
            results = []
            threads = [threading.Thread(target=lambda: results.append(self.chat(base))) for _ in range(2)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            took = time.time() - t0
        self.assertEqual([r[0] for r in results], [200, 200])
        self.assertEqual(slow.peak, 2)  # both ran at the same time
        self.assertLess(took, 1.1)  # ~0.6 s for both, not ~1.2 s one after the other

    def test_full_queue_answers_busy(self):
        slow = Slow()
        with mock.patch("libra.server.stream_text", slow):
            base, engine = self.start(max_generations=1, queue_wait=0.1)
            first = threading.Thread(target=self.chat, args=(base,))
            first.start()
            time.sleep(0.15)
            status, headers, _ = self.chat(base)
            first.join()
            self.assertEqual(status, 503)
            self.assertEqual(headers.get("Retry-After"), "5")
            self.assertEqual(self.chat(base)[0], 200)  # the slot was released afterwards

    def test_rate_limit_and_input_caps(self):
        base, _ = self.start(rate_limit=3)
        many = {"messages": [{"role": "user", "content": "x"}] * 201}
        self.assertEqual(self.post(base, "/v1/chat/completions", many)[0], 400)
        self.assertEqual(self.post(base, "/v1/agent/plan", {"goal": "x" * 2001})[0], 400)
        self.assertNotEqual(self.post(base, "/v1/translate", {"text": "hi", "to": "es"})[0], 429)  # 3rd: still allowed
        status, headers, _ = self.post(base, "/v1/translate", {"text": "hi", "to": "es"})
        self.assertEqual(status, 429)  # the 4th in a minute is over the limit
        self.assertIn("Retry-After", headers)
        self.assertEqual(self.get(base, "/v1/info")[0], 200)  # cheap requests aren't limited

    def test_connection_cap_and_deadline(self):
        import libra.server as server

        with mock.patch.object(server, "SOCKET_TIMEOUT", 1):
            base, _ = self.start(limits={"per_address": 2})
        port = int(base.rsplit(":", 1)[1])
        idle = [socket.create_connection(("127.0.0.1", port)) for _ in range(2)]  # open, but send nothing
        time.sleep(0.2)
        third = socket.create_connection(("127.0.0.1", port))
        third.settimeout(5)
        self.assertIn(b"503", third.recv(200))  # over the per-address cap: refused straight away
        idle[0].settimeout(5)
        t0 = time.time()
        self.assertEqual(idle[0].recv(10), b"")  # the silent client is disconnected by the deadline
        self.assertLess(time.time() - t0, 4)
        for s in idle + [third]:
            s.close()

    def test_device_tokens(self):
        from libra import devices

        base, _ = self.start()
        status, _, raw = self.post(base, "/v1/pair", {"base": "https://libra.test", "name": "Ana's phone"})
        ana = json.loads(raw)["url"].split("#token=")[1]
        self.assertTrue(ana.startswith("dev_"))
        with open(os.path.join(self.tmp.name, "devices.json")) as f:
            self.assertNotIn(ana, f.read())  # only a hash is stored
        self.assertEqual(self.get(base, "/v1/info", token=ana)[0], 200)
        status, _, raw = self.post(base, "/v1/pair", {"base": "https://libra.test", "name": "tablet"}, token=ana)
        tablet = json.loads(raw)["url"].split("#token=")[1]
        _, owners = self.post(base, "/v1/pair", {"base": "https://libra.test", "name": "laptop"})[:2]
        listing = self.get(base, "/v1/devices")[1]["devices"]
        ids = {d["name"]: d["id"] for d in listing}
        self.assertEqual(self.post(base, "/v1/devices/revoke", {"id": ids["laptop"]}, token=ana)[0], 403)
        self.assertEqual(self.post(base, "/v1/devices/revoke", {"id": ids["Ana's phone"]})[0], 200)
        self.assertEqual(self.get(base, "/v1/info", token=ana)[0], 401)  # revoked
        self.assertEqual(self.get(base, "/v1/info", token=tablet)[0], 401)  # and the device it paired
        self.assertEqual([d["name"] for d in devices.listing()], ["laptop"])
        self.assertEqual(self.get(base, "/v1/info")[0], 200)  # the main token still works


class RouteAuditTest(unittest.TestCase):
    """Every way a skill can be started goes through the trust gate."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = self.tmp.name
        from libra.skills import default_registry
        from libra.trust import Policy

        self.calls = []
        self.reg = default_registry(log=None, load_default_dir=False, catalog=None, policy=Policy())

        @self.reg.skill("wipe_backups", "Delete all backups", router=lambda m: {} if m == "wipe backups" else None,
                        risk="danger")
        def wipe_backups():
            self.calls.append("wiped")
            return "wiped"

    def tearDown(self):
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def test_discord_timers_and_autonomy_respect_trust(self):
        from libra import agent
        from libra.autonomy import pursue
        from libra.discord import Bot

        bot = Bot("t", self.reg, config={"allowed_users": ["1"]}, log=None)
        self.assertIn("approval", bot.respond("wipe backups", "1"))  # even an allowed Discord user
        self.assertEqual(pursue("wipe backups", self.reg, remember=False).status, "blocked")
        agent.add_goal("nightly", "wipe backups", "hourly", self.reg)
        self.assertEqual(agent.run_due(self.reg, log=None)[0].status, "blocked")  # scheduled runs too
        self.assertEqual(self.calls, [])
        self.reg.policy.set("wipe_backups", "allow")
        self.reg.policy.set("wipe", "deny")  # unrelated prefix: no effect
        self.assertEqual(self.reg.run("wipe_backups").text, "wiped")


if __name__ == "__main__":
    unittest.main()
