"""The Discord bot against a fake Discord (gateway over WebSocket + REST API)."""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures"))
from ws_server import Server  # noqa: E402

BOT_ID = "999"


class FakeREST(BaseHTTPRequestHandler):
    posts: list = []
    limited = {"done": False}

    def log_message(self, *a):
        pass

    def _reply(self, status, obj):
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        assert self.headers["Authorization"] == "Bot test-token"
        if self.path.endswith("/users/@me"):
            return self._reply(200, {"id": BOT_ID, "username": "libra-test"})
        self._reply(404, {"message": "Unknown"})

    def do_POST(self):
        assert self.headers["Authorization"] == "Bot test-token"
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        if self.path.endswith("/typing"):
            self.send_response(204)
            self.end_headers()
            return
        if not FakeREST.limited["done"]:  # the first message is rate limited once
            FakeREST.limited["done"] = True
            return self._reply(429, {"message": "You are being rate limited.", "retry_after": 0.05})
        msg = json.loads(body)
        FakeREST.posts.append({"channel": self.path.split("/")[-2], "content": msg["content"],
                               "reply_to": (msg.get("message_reference") or {}).get("message_id"),
                               "allowed_mentions": msg.get("allowed_mentions")})
        self._reply(200, {"id": str(len(FakeREST.posts))})


def message(mid, content, author="111", guild=True, mentions=(), bot=False, channel="5001"):
    d = {"id": mid, "channel_id": channel, "content": content, "author": {"id": author, "username": "u", "bot": bot},
         "mentions": [{"id": m} for m in mentions]}
    if guild:
        d["guild_id"] = "g1"
    return {"op": 0, "t": "MESSAGE_CREATE", "s": None, "d": d}


class DiscordTest(unittest.TestCase):
    def setUp(self):
        FakeREST.posts, FakeREST.limited = [], {"done": False}
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = self.tmp.name
        self.rest = ThreadingHTTPServer(("127.0.0.1", 0), FakeREST)
        threading.Thread(target=self.rest.serve_forever, daemon=True).start()
        self.seen = {"identify": None, "resume": None, "heartbeats": 0}

    def tearDown(self):
        self.rest.shutdown()
        self.rest.server_close()
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def wait_posts(self, n, timeout=20):
        end = time.time() + timeout
        while len(FakeREST.posts) < n and time.time() < end:
            time.sleep(0.05)

    def gateway(self, conn):
        seq = [0]

        def send(obj):
            seq[0] += 1
            if obj.get("op") == 0:
                obj["s"] = seq[0]
            conn.send_text(json.dumps(obj))

        conn.send_text(json.dumps({"op": 10, "d": {"heartbeat_interval": 150}}))

        def reader(stop):
            while not stop.is_set():
                try:
                    op, data = conn.recv()
                except (ConnectionError, OSError):
                    return  # the bot disconnected (expected at reconnects)
                if op != 1:
                    continue
                m = json.loads(data)
                if m["op"] == 1:
                    self.seen["heartbeats"] += 1
                    conn.send_text(json.dumps({"op": 11}))
                elif m["op"] == 2:
                    self.seen["identify"] = m["d"]
                elif m["op"] == 6:
                    self.seen["resume"] = m["d"]

        stop = threading.Event()
        threading.Thread(target=reader, args=(stop,), daemon=True).start()
        time.sleep(0.2)
        if self.seen["resume"] is None and self.seen["identify"] and self.connection == 1:
            send({"op": 0, "t": "READY", "d": {"user": {"id": BOT_ID, "username": "libra-test"}, "session_id": "sess1",
                                              "resume_gateway_url": f"ws://127.0.0.1:{self.ws.port}"}})
            for m in [message("m1", "what is 6*7", guild=False),                    # DM: answered
                      message("m2", "hello everyone"),                              # not for the bot: ignored
                      message("m3", f"<@{BOT_ID}> help", mentions=[BOT_ID]),         # mention
                      message("m4", "!goal what is 2+2", author="222"),             # not allowed
                      message("m5", "!remember the backup runs at 3am"),            # allowed user
                      message("m6", "!help", author="333", bot=True)]:              # another bot: ignored
                send(m)
            self.wait_posts(4)
            send(message("m7", "!recall backup"))
            send(message("m8", "!goal what is (1+2)*3"))
            self.wait_posts(7)
            time.sleep(0.4)  # let a few heartbeats happen
            send({"op": 7, "d": None})  # please reconnect
            time.sleep(0.5)
        else:
            time.sleep(0.3)
            send(message("m9", "!skills arithmetic"))
            self.wait_posts(8)
            conn.close(4004, "Authentication failed.")
        stop.set()

    def test_full_session(self):
        from libra.discord import Bot, DiscordError
        from libra.skills import default_registry
        from libra.trust import Policy

        self.connection = 0

        def handler(conn):
            self.connection += 1
            self.gateway(conn)

        self.ws = Server(handler)
        reg = default_registry(log=None, load_default_dir=False, catalog=None, policy=Policy())
        bot = Bot("test-token", reg, config={"allowed_users": ["111"], "prefix": "!"},
                  api_base=f"http://127.0.0.1:{self.rest.server_address[1]}/api/v10",
                  gateway=f"ws://127.0.0.1:{self.ws.port}/?v=10&encoding=json", log=lambda *a: None)
        with self.assertRaises(DiscordError) as cm:  # 4004 ends the session for good
            bot.run_forever()
        self.assertIn("token was rejected", str(cm.exception))
        self.ws.stop()

        ident = self.seen["identify"]
        self.assertEqual(ident["token"], "test-token")
        self.assertEqual(ident["intents"], (1 << 0) | (1 << 9) | (1 << 12))  # no privileged intent needed
        self.assertEqual(self.seen["resume"]["session_id"], "sess1")  # reconnected and resumed
        self.assertGreater(self.seen["resume"]["seq"], 0)
        self.assertGreaterEqual(self.seen["heartbeats"], 2)
        replies = {p["reply_to"]: p["content"] for p in FakeREST.posts}
        self.assertEqual(replies["m1"], "6*7 = 42")
        self.assertIn("ask <question>", replies["m3"])
        self.assertIn("allow list", replies["m4"])
        self.assertIn("I'll remember that", replies["m5"])
        self.assertIn("3am", replies["m7"])
        self.assertTrue(any("(1+2)*3 = 9" in p["content"] for p in FakeREST.posts if p["reply_to"] == "m8"))
        self.assertTrue(any("Working on it" in p["content"] for p in FakeREST.posts if p["reply_to"] == "m8"))
        self.assertIn("`calc`", replies["m9"])
        self.assertNotIn("m2", replies)
        self.assertNotIn("m6", replies)
        self.assertTrue(all(p["allowed_mentions"] == {"parse": []} for p in FakeREST.posts))  # never pings anyone

    def test_split_and_addressing(self):
        from libra.discord import Bot, split_message

        parts = split_message(("word " * 300 + "\n\n") * 3)
        self.assertTrue(all(len(p) <= 2000 for p in parts))
        self.assertEqual(" ".join(parts).split(), (("word " * 300) * 3).split())
        bot = Bot("t", None, config={"channels": ["5001"], "prefix": "!"}, log=None)
        bot.user_id = BOT_ID
        self.assertEqual(bot.addressed(message("x", f"<@!{BOT_ID}> hi", mentions=[BOT_ID])["d"]), "hi")
        self.assertIsNone(bot.addressed(message("x", "!help", channel="other")["d"]))  # not an allowed channel
        self.assertEqual(bot.addressed(message("x", "hi", guild=False, channel="dm")["d"]), "hi")  # DMs always
        bot.cfg["dm"] = False
        self.assertIsNone(bot.addressed(message("x", "hi", guild=False, channel="dm")["d"]))
        bot.cfg["listen_channels"] = ["c2"]
        self.assertEqual(bot.addressed(message("x", "anything", channel="c2")["d"]), "anything")
        self.assertTrue(bot.intents & (1 << 15))  # listening to a channel needs Message Content

    def test_without_message_content_servers_need_a_mention(self):
        from libra.discord import Bot

        bot = Bot("t", None, config={"prefix": "!"}, log=None)
        bot.user_id, bot.username = BOT_ID, "Libra"
        self.assertFalse(bot.reads_server_text)
        self.assertFalse(bot.intents & (1 << 15))
        # what Discord really sends a bot without the intent: "!help" in a server arrives with no text
        hidden = message("x", "", mentions=[])["d"]
        self.assertIsNone(bot.addressed(hidden))
        # so the help shows commands as @mentions in servers, and says why the prefix doesn't work there
        text = bot.respond("help", "111")
        self.assertIn("`@Libra ask <question>`", text)
        self.assertIn("Message Content intent", text)
        self.assertNotIn("`!ask", text)

        bot.cfg["message_content"] = True  # the owner enabled the intent in the portal
        self.assertTrue(bot.intents & (1 << 15))
        self.assertIn("`!ask <question>`", bot.respond("help", "111"))
        self.assertEqual(bot.addressed(message("x", "!help")["d"]), "help")

    def test_set_message_content(self):
        import contextlib
        import io

        from libra import discord
        from libra.cli import main

        with contextlib.redirect_stdout(io.StringIO()):
            main(["discord", "set", "message_content", "on"])
        self.assertTrue(discord.load_config()["message_content"])
        with contextlib.redirect_stdout(io.StringIO()):
            main(["discord", "set", "message_content", "off"])
        self.assertFalse(discord.load_config()["message_content"])


if __name__ == "__main__":
    unittest.main()


class FakeChannels(BaseHTTPRequestHandler):
    """A fake Discord REST API that keeps messages and reactions, for the Discord skills."""
    messages: dict = {}   # channel -> [message, newest last]
    requests: list = []

    def log_message(self, *a):
        pass

    def _reply(self, status, obj=None):
        data = json.dumps(obj).encode() if obj is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _find(self, channel, mid):
        return next((m for m in FakeChannels.messages.get(channel, []) if m["id"] == mid), None)

    def do_GET(self):
        assert self.headers["Authorization"] == "Bot test-token"
        FakeChannels.requests.append(("GET", self.path))
        parts = self.path.split("?")[0].split("/")
        if self.path.endswith("/users/@me"):
            return self._reply(200, {"id": BOT_ID, "username": "libra-test"})
        if len(parts) == 6 and parts[-1] == "messages":
            return self._reply(200, list(reversed(FakeChannels.messages.get(parts[-2], []))))
        if len(parts) == 7 and parts[-2] == "messages":
            m = self._find(parts[-3], parts[-1])
            return self._reply(200, m) if m else self._reply(404, {"message": "Unknown Message"})
        self._reply(404, {"message": "Unknown"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        FakeChannels.requests.append(("POST", self.path))
        channel = self.path.split("/")[-2]
        msgs = FakeChannels.messages.setdefault(channel, [])
        m = {"id": str(9000 + sum(len(v) for v in FakeChannels.messages.values())), "content": body["content"],
             "author": {"id": BOT_ID, "username": "libra-test"}, "timestamp": "2026-11-21T10:00:00",
             "allowed_mentions": body.get("allowed_mentions")}
        msgs.append(m)
        self._reply(200, m)

    def do_PUT(self):
        from urllib.parse import unquote

        FakeChannels.requests.append(("PUT", self.path))
        parts = self.path.split("/")  # /api/v10/channels/C/messages/M/reactions/E/@me
        m = self._find(parts[4], parts[6])
        if m is None:
            return self._reply(404, {"message": "Unknown Message"})
        m.setdefault("reactions", []).append({"emoji": {"name": unquote(parts[8]), "id": None}, "me": True, "count": 1})
        self._reply(204)


class DiscordSkillsTest(unittest.TestCase):
    def setUp(self):
        from unittest import mock

        from libra import discord

        FakeChannels.messages = {"5001": [
            {"id": "1", "content": "the release is ready", "author": {"id": "111", "username": "ana"},
             "timestamp": "2026-11-21T09:00:00"},
            {"id": "2", "content": "", "author": {"id": "222", "username": "bo"}, "timestamp": "2026-11-21T09:05:00"}]}
        FakeChannels.requests = []
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = [mock.patch.dict(os.environ, {"LIBRA_HOME": self.tmp.name, "DISCORD_TOKEN": "test-token"})]
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeChannels)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.patches.append(mock.patch.object(discord, "API", f"http://127.0.0.1:{self.srv.server_address[1]}/api/v10"))
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.srv.shutdown()
        self.srv.server_close()
        self.tmp.cleanup()

    def registry(self, allow=()):
        from libra.skills import default_registry
        from libra.trust import Policy

        return default_registry(log=None, load_default_dir=False, catalog=None,
                                policy=Policy({"rules": {s: "allow" for s in allow}}, save=False))

    def test_only_with_a_bot_token(self):
        from unittest import mock

        with mock.patch.dict(os.environ, {"DISCORD_TOKEN": ""}):
            self.assertNotIn("discord_post", self.registry())
        reg = self.registry()
        self.assertEqual([reg.get(s).risk for s in ("discord_history", "discord_post", "discord_react")],
                         ["read", "write", "write"])
        self.assertTrue(reg.get("discord_history").untrusted_output)  # other people wrote it

    def test_read_post_react_and_check_the_effect(self):
        reg = self.registry(allow=["discord_post", "discord_react"])
        r = reg.run("discord_history", {"channel": "5001"})
        self.assertEqual(r.text.splitlines()[0], "[2026-11-21 09:00] ana: the release is ready")  # oldest first
        self.assertIn("Message Content intent", r.text)  # an empty message says why

        posted = reg.run("discord_post", {"channel": "5002", "text": "hello <@everyone>"})
        self.assertTrue(posted.ok)
        sent = FakeChannels.messages["5002"][0]
        self.assertEqual(sent["allowed_mentions"], {"parse": []})  # never pings anyone
        skill = reg.get("discord_post")
        self.assertTrue(skill.verify({"channel": "5002", "text": "hello <@everyone>"}, posted))
        sent["content"] = "edited by someone"
        self.assertFalse(skill.verify({"channel": "5002", "text": "hello <@everyone>"}, posted))

        self.assertTrue(reg.run("discord_react", {"channel": "5001", "message": "1", "emoji": "👍"}).ok)
        self.assertTrue(reg.get("discord_react").verify({"channel": "5001", "message": "1", "emoji": "👍"}, None))
        self.assertFalse(reg.get("discord_react").verify({"channel": "5001", "message": "1", "emoji": "🎉"}, None))

    def test_ids_and_emoji_cannot_change_the_api_path(self):
        reg = self.registry(allow=["discord_post", "discord_react"])
        for args in ({"channel": "../../users/@me", "text": "x"}, {"channel": "5001/messages", "text": "x"}):
            self.assertFalse(reg.run("discord_post", args).ok)
        self.assertFalse(reg.run("discord_react", {"channel": "5001", "message": "1", "emoji": "../x"}).ok)
        self.assertFalse(reg.run("discord_history", {"channel": "5001?limit=100&x"}).ok)
        self.assertEqual(FakeChannels.requests, [])  # nothing was sent

    def test_channel_text_cannot_post_by_itself_and_a_crash_is_reconciled(self):
        from libra import jobs

        reg = self.registry(allow=["discord_post"])  # even with posting allowed
        goal = "read discord channel 5001 then post it to discord channel 5002"
        job = jobs.run(jobs.create(goal, reg)["id"], reg, retry_delay=0)
        self.assertEqual((job["status"], job["waiting"]["kind"]), ("waiting", "untrusted_input"))
        self.assertNotIn("5002", FakeChannels.messages)
        jobs.approve(job["id"])
        job = jobs.run(job["id"], reg, retry_delay=0)
        self.assertEqual((job["status"], job["result"]["effects"][0]["status"]), ("done", "passed"))
        self.assertEqual(len(FakeChannels.messages["5002"]), 1)

        # the runner died during the post, after Discord got it: reconcile sees it, so it isn't posted twice
        j = jobs.get(job["id"])
        j["status"], j["result"], j["steps"][1]["status"] = "running", None, "running"
        jobs._save(j)
        job = jobs.run(job["id"], reg, retry_delay=0)
        self.assertEqual(job["status"], "done")
        self.assertEqual(len(FakeChannels.messages["5002"]), 1)
