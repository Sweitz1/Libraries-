"""What the Android app relies on: the libra:// pairing link, the same token in both links, the
latest training decision in /v1/auto/status, and the web app's hooks for the app."""
import json
import os
import tempfile
import unittest
from urllib.parse import parse_qs, urlsplit


class MobileContractTest(unittest.TestCase):
    def test_app_link_round_trips(self):
        from libra.pair import app_link, link

        url = app_link("https://atlas.tail1234.ts.net/", "dev_abc-DEF_123")
        self.assertTrue(url.startswith("libra://pair?"))
        q = parse_qs(urlsplit(url).query)
        self.assertEqual(q, {"base": ["https://atlas.tail1234.ts.net"], "token": ["dev_abc-DEF_123"]})
        self.assertEqual(app_link("http://10.0.0.2:8000", None), "libra://pair?base=http%3A%2F%2F10.0.0.2%3A8000")
        self.assertEqual(link("http://10.0.0.2:8000", "t"), "http://10.0.0.2:8000/#token=t")

    def test_pair_endpoint_offers_the_app_link(self):
        import threading
        import urllib.request

        from libra.server import Engine, make_server

        with tempfile.TemporaryDirectory() as d:
            old = os.environ.get("LIBRA_HOME")
            os.environ["LIBRA_HOME"] = d
            try:
                srv = make_server(Engine(None, None, token="main"), "127.0.0.1", 0)
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                req = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/v1/pair",
                                             json.dumps({"base": "https://atlas.example", "name": "phone"}).encode(),
                                             {"Authorization": "Bearer main", "Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as r:
                    out = json.loads(r.read())
                srv.shutdown()
                srv.server_close()
            finally:
                if old is None:
                    os.environ.pop("LIBRA_HOME", None)
                else:
                    os.environ["LIBRA_HOME"] = old
        token = out["url"].split("#token=")[1]
        self.assertEqual(parse_qs(urlsplit(out["app_url"]).query)["token"], [token])  # one device token, two forms
        self.assertIn("<svg", out["app_svg"])

    def test_last_training_run(self):
        from libra import auto

        with tempfile.TemporaryDirectory() as d:
            cfg = json.loads(json.dumps(auto.TEMPLATE))
            cfg.update(dataset=os.path.join(d, "ds"), model=os.path.join(d, "m"))
            path = os.path.join(d, "auto.json")
            with open(path, "w") as f:
                json.dump(cfg, f)
            self.assertEqual(auto.last_run(path), {})
            os.makedirs(os.path.join(d, "ds", ".auto"))
            with open(os.path.join(d, "ds", ".auto", "runs.jsonl"), "w") as f:
                f.write(json.dumps({"finished": "t1", "decision": "kept the old model"}) + "\n")
                f.write(json.dumps({"finished": "t2", "decision": "kept the new model"}) + "\n")
            self.assertEqual(auto.last_run(path), {"finished": "t2", "decision": "kept the new model"})

    def test_web_app_hooks_for_the_android_app(self):
        js = open(os.path.join(os.path.dirname(__file__), "..", "libra", "webui", "app.js"), encoding="utf-8").read()
        self.assertIn("LibraAndroid", js)        # it knows when it runs inside the app
        self.assertIn("libra://signout", js)     # sign-out also clears the app's saved sign-in
        self.assertIn("tab=([a-z]+)", js)        # notifications open a tab


if __name__ == "__main__":
    unittest.main()
