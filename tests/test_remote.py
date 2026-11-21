"""Phone/web access: token auth, lockout, the app's files and endpoints, model reload, installer."""
import json
import os
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class RemoteServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = os.path.join(self.tmp.name, "home")
        from libra import autograd as ag
        from libra import checkpoint
        from libra.knowledge import Workspace
        from libra.nn import GPT, GPTConfig
        from libra.server import Engine, make_server
        from libra.skills import default_registry
        from libra.tokenizer import BPETokenizer
        from libra.translate import Translator, read_pairs

        ag.seed(0)
        tok = BPETokenizer.train("hello world " * 30, 280)
        self.model_dir = os.path.join(self.tmp.name, "model")
        checkpoint.save(self.model_dir, GPT(GPTConfig(tok.vocab_size, 16, 1, 2, 16, 0.0)), tok, {"step": 1})
        model, tok, _ = checkpoint.load(self.model_dir)
        notes = os.path.join(self.tmp.name, "notes.md")
        with open(notes, "w") as f:
            f.write("The Falcon budget for 2027 is 40,000 dollars, approved by Dana in October.\n")
        ws = Workspace(os.path.join(self.tmp.name, "ws"))
        ws.add([notes], log=None)
        Translator().add_pairs(read_pairs(os.path.join(ROOT, "examples", "phrasebook_en_es.tsv"), "en", "es"), "en", "es")
        self.engine = Engine(model, tok, skills=default_registry(log=None, load_default_dir=False, catalog=None),
                             workspace=ws, token="s3cret-token")
        self.engine.watch_model(self.model_dir)
        self.server = make_server(self.engine, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def req(self, path, body=None, token="s3cret-token", ip_headers=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = json.dumps(body).encode() if body is not None else None
        r = urllib.request.Request(self.base + path, data, headers)
        try:
            with urllib.request.urlopen(r, timeout=20) as resp:
                raw = resp.read()
                return resp.status, resp.headers, raw
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()

    def test_auth_and_lockout(self):
        self.assertEqual(self.req("/v1/info", token=None)[0], 401)
        self.assertEqual(self.req("/v1/info", token="nope")[0], 401)
        self.assertEqual(self.req("/health", token=None)[0], 200)  # open for monitoring
        status, headers, raw = self.req("/", token=None)  # the app's page loads, then asks for the token
        self.assertEqual(status, 200)
        self.assertIn(b"<title>Libra</title>", raw)
        self.assertIn("script-src 'self'", headers["Content-Security-Policy"])
        self.assertEqual(self.req("/app.js", token=None)[0], 200)
        info = json.loads(self.req("/v1/info")[2])
        self.assertEqual((info["model"], info["workspace"]["files"], info["translate"], info["auth"]),
                         (True, 1, True, True))
        for _ in range(9):
            self.req("/v1/info", token="wrong")
        self.assertEqual(self.req("/v1/info", token="wrong")[0], 429)  # locked out after repeated failures
        self.assertEqual(self.req("/v1/info")[0], 429)  # even the right token, for a while, from that address

    def test_app_endpoints(self):
        status, _, raw = self.req("/v1/translate", {"text": "Where is the hotel?", "to": "es", "source": "en"})
        self.assertEqual(json.loads(raw)["text"], "¿Dónde está el hotel?")
        self.assertEqual(self.req("/v1/translate", {"to": "es"})[0], 400)
        self.assertEqual(self.req("/v1/auto/status")[0], 404)  # no --auto-config
        msgs = [{"role": "user", "content": "What is the Falcon budget?"}]
        grounded = json.loads(self.req("/v1/chat/completions", {"messages": msgs})[2])
        self.assertIn("40,000", grounded["choices"][0]["message"]["content"])
        model = json.loads(self.req("/v1/chat/completions", {"messages": msgs, "libra_mode": "model",
                                                              "max_tokens": 3})[2])
        self.assertNotIn("libra_grounding", model)  # answered by the model, not the files
        calc = json.loads(self.req("/v1/chat/completions", {"messages": [{"role": "user", "content": "what is 6*7"}],
                                                             "libra_auto_skills": True})[2])
        self.assertEqual(calc["choices"][0]["message"]["content"], "6*7 = 42")
        self.assertEqual(self.req("/v1/skills/calc", {"expression": "1+1"}, token=None)[0], 401)
        big = {"messages": [{"role": "user", "content": "x" * 2_100_000}]}
        self.assertEqual(self.req("/v1/chat/completions", big)[0], 413)

    def test_model_reloads_after_training(self):
        from libra import checkpoint
        from libra.nn import GPT, GPTConfig

        old = self.engine.model
        time.sleep(0.05)
        _, tok, _ = checkpoint.load(self.model_dir)
        checkpoint.save(self.model_dir, GPT(GPTConfig(tok.vocab_size, 16, 2, 2, 16, 0.0)), tok, {"step": 2})
        os.utime(os.path.join(self.model_dir, "model.npz"))
        info = json.loads(self.req("/v1/info")[2])
        self.assertIsNot(self.engine.model, old)
        self.assertEqual(self.engine.model.config.n_layer, 2)
        self.assertGreater(info["model_parameters"], 0)


class AutoEndpointTest(unittest.TestCase):
    def test_run_and_status(self):
        from libra import auto
        from libra.server import Engine, make_server

        with tempfile.TemporaryDirectory() as d:
            cfg = os.path.join(d, "auto.json")
            auto.write_template(cfg)
            with open(cfg) as f:
                c = json.load(f)
            c["enabled"] = False
            with open(cfg, "w") as f:
                json.dump(c, f)
            server = make_server(Engine(None, None, token="t", auto_config=cfg), "127.0.0.1", 0)
            base = f"http://127.0.0.1:{server.server_address[1]}"
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                def call(path, body=None):
                    r = urllib.request.Request(base + path, json.dumps(body).encode() if body is not None else None,
                                               {"Authorization": "Bearer t", "Content-Type": "application/json"})
                    with urllib.request.urlopen(r, timeout=20) as resp:
                        return resp.status, json.load(resp)

                self.assertEqual(call("/v1/auto/run", {})[0], 202)
                for _ in range(50):
                    status = call("/v1/auto/status")[1]
                    if not status["running"]:
                        break
                    time.sleep(0.1)
                self.assertIn("switched off", status["last"]["decision"])
            finally:
                server.shutdown()
                server.server_close()


class TokenAndInstallerTest(unittest.TestCase):
    def test_token_rules(self):
        import argparse

        from libra.cli import _server_token

        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("LIBRA_HOME")
            os.environ["LIBRA_HOME"] = d
            try:
                a = argparse.Namespace(no_auth=False, token=None, token_file=None, host="127.0.0.1")
                self.assertIsNone(_server_token(a))  # local only: no token needed
                a.host = "0.0.0.0"
                t1 = _server_token(a)
                self.assertGreater(len(t1), 20)  # network address: a token is created and saved
                self.assertEqual(_server_token(a), t1)
                self.assertEqual(oct(os.stat(os.path.join(d, "server-token")).st_mode & 0o777), "0o600")
                a.no_auth = True
                self.assertIsNone(_server_token(a))
            finally:
                if old is None:
                    os.environ.pop("LIBRA_HOME", None)
                else:
                    os.environ["LIBRA_HOME"] = old

    def test_installer_syntax(self):
        script = os.path.join(ROOT, "deploy", "install-debian.sh")
        self.assertEqual(subprocess.run(["bash", "-n", script]).returncode, 0)
        bad = subprocess.run(["bash", script, "--root", "/tmp/x", "--train-at", "25:00"], capture_output=True, text=True)
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("HH:MM", bad.stderr)


if __name__ == "__main__":
    unittest.main()
