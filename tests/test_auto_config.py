"""The training configuration: auto.editable_config / auto.save_editable, and the
GET/POST /v1/auto/config endpoints (owner-only writes, validation, atomic save)."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


class ConfigHelpersTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "libra-auto.json")

    def tearDown(self):
        self.dir.cleanup()

    def test_missing_file_returns_template(self):
        from libra import auto

        cfg = auto.editable_config(self.path)
        self.assertIn("sources", cfg)
        self.assertTrue(cfg["sources"])

    def test_save_toggles_and_replaces_sources(self):
        from libra import auto

        auto.write_template(self.path)
        saved = auto.save_editable(self.path, {
            "enabled": False, "name": "mine", "steps_per_run": 50,
            "sources": [{"crawl": "https://example.com/docs/", "max_pages": 7, "depth": 3},
                        {"gutenberg": "1342"}, {"files": "~/notes"}],
        })
        self.assertFalse(saved["enabled"])
        self.assertEqual(saved["name"], "mine")
        self.assertEqual(saved["steps_per_run"], 50)
        self.assertEqual(len(saved["sources"]), 3)
        # written to disk as valid JSON
        with open(self.path) as f:
            on_disk = json.load(f)
        self.assertEqual(on_disk["sources"][0]["max_pages"], 7)
        self.assertEqual(on_disk["sources"][0]["depth"], 3)        # extra per-row field round-trips
        self.assertEqual(on_disk["sources"][1]["gutenberg"], 1342)  # coerced to int

    def test_preserves_unlisted_top_level_fields(self):
        from libra import auto

        auto.write_template(self.path)
        before = auto.editable_config(self.path)
        auto.save_editable(self.path, {"enabled": True})
        after = auto.editable_config(self.path)
        self.assertEqual(after.get("limits"), before.get("limits"))
        self.assertEqual(after.get("gate"), before.get("gate"))

    def test_rejects_bad_input(self):
        from libra import auto

        auto.write_template(self.path)
        for bad in (
            {"sources": []},                                   # empty
            {"sources": [{}]},                                 # no kind
            {"sources": [{"crawl": "ftp://nope"}]},            # non-http
            {"sources": [{"crawl": "https://a", "url": "https://b"}]},  # two kinds
            {"sources": [{"gutenberg": "not-a-number"}]},      # bad id
            {"steps_per_run": "lots"},                         # non-numeric
        ):
            with self.assertRaises(ValueError):
                auto.save_editable(self.path, bad)
        # a failed save leaves the previous file intact and valid
        with open(self.path) as f:
            json.load(f)

    def test_source_option_bounds(self):
        from libra import auto

        auto.write_template(self.path)
        saved = auto.save_editable(self.path, {"sources": [{"crawl": "https://x/", "max_pages": 999999}]})
        self.assertLessEqual(saved["sources"][0]["max_pages"], 5000)


class ConfigEndpointTest(unittest.TestCase):
    def setUp(self):
        from libra import autograd as ag
        from libra.nn import GPT, GPTConfig
        from libra.server import Engine, make_server
        from libra.tokenizer import BPETokenizer

        self.home = tempfile.TemporaryDirectory()
        os.environ["LIBRA_HOME"] = self.home.name
        self.cfgdir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.cfgdir.name, "libra-auto.json")
        from libra import auto
        auto.write_template(self.path)

        ag.seed(0)
        tok = BPETokenizer.train("hello world " * 20, 280)
        model = GPT(GPTConfig(tok.vocab_size, 16, 1, 2, 16, 0.0)).eval()
        self.token = "owner-secret-token-123456"
        self.server = make_server(Engine(model, tok, token=self.token, auto_config=self.path), "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.home.cleanup()
        self.cfgdir.cleanup()
        os.environ.pop("LIBRA_HOME", None)

    def _req(self, method, path, token, body=None):
        headers = {"Content-Type": "application/json", "X-Libra-CSRF": "1"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data, headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=20) as r:
                return r.status, json.load(r)
        except urllib.error.HTTPError as e:
            return e.code, json.load(e)

    def test_get_and_owner_save(self):
        status, body = self._req("GET", "/v1/auto/config", self.token)
        self.assertEqual(status, 200)
        self.assertTrue(body["can_edit"])
        self.assertIn("url", "".join(k for s in body["sources"] for k in s) or "url")

        status, body = self._req("POST", "/v1/auto/config", self.token,
                                 {"enabled": True, "sources": [{"url": "https://example.org/a"}]})
        self.assertEqual(status, 200, body)
        self.assertTrue(body["saved"])
        self.assertEqual(body["sources"], [{"url": "https://example.org/a"}])

    def test_write_needs_owner(self):
        from libra import devices

        dev_token, _ = devices.create("a phone", created_by="owner")
        status, body = self._req("GET", "/v1/auto/config", dev_token)
        self.assertEqual(status, 200)
        self.assertFalse(body["can_edit"])                 # a device may read
        status, body = self._req("POST", "/v1/auto/config", dev_token,
                                 {"sources": [{"url": "https://evil.example/x"}]})
        self.assertEqual(status, 403)                      # but not write

    def test_bad_body_is_400(self):
        status, body = self._req("POST", "/v1/auto/config", self.token, {"sources": [{"crawl": "ftp://nope"}]})
        self.assertEqual(status, 400)
        self.assertIn("http", body["error"]["message"])


if __name__ == "__main__":
    unittest.main()
