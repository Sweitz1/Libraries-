"""Browser sessions (fifth review, P1): the web app's sign-in is an HttpOnly, SameSite=Strict cookie
instead of a token in page storage. Cookie-signed-in changes need the app's own header and origin
(no CSRF). A session ends at sign-out, when its device is revoked, or when the main token changes.
Bearer tokens keep working for scripts and apps."""
import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


class SessionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = self.tmp.name
        self.servers = []
        self.base = self.serve("main-token")

    def serve(self, token):
        from libra.server import Engine, make_server

        srv = make_server(Engine(None, None, token=token), "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        return f"http://127.0.0.1:{srv.server_address[1]}"

    def tearDown(self):
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def req(self, path, body=None, base=None, **headers):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request((base or self.base) + path, data=data, headers=headers,
                                   method="POST" if body is not None else "GET")
        try:
            with urllib.request.urlopen(r, timeout=10) as resp:
                return resp.status, resp.headers, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as e:
            return e.code, e.headers, json.loads(e.read() or b"{}")

    def sign_in(self, token="main-token", **headers):
        status, h, out = self.req("/v1/session", {}, Authorization=f"Bearer {token}", **headers)
        self.assertEqual(status, 200, out)
        cookie = h["Set-Cookie"]
        return cookie.split(";")[0], cookie, out  # "libra_session=ses_…"

    def test_the_cookie_is_out_of_reach_of_scripts_and_other_sites(self):
        pair, raw, out = self.sign_in()
        self.assertEqual(out["principal"], "owner")
        for flag in ("HttpOnly", "SameSite=Strict", "Path=/", "Max-Age=2592000"):
            self.assertIn(flag, raw)
        self.assertNotIn("Secure", raw)  # plain HTTP here: a Secure cookie would never come back
        self.assertTrue(pair.startswith("libra_session=ses_"))
        self.assertNotIn("main-token", raw)  # the cookie never carries a token
        with open(os.path.join(self.tmp.name, "sessions.json"), encoding="utf-8") as f:
            self.assertNotIn("main-token", f.read())  # only hashes
        self.assertIn("Secure", self.sign_in(**{"X-Forwarded-Proto": "https"})[1])  # behind an HTTPS proxy

        self.assertEqual(self.req("/v1/info", Cookie=pair)[0], 200)  # reads need only the cookie
        self.assertEqual(self.req("/v1/devices", Cookie=pair)[2]["you"], "owner")

        # changes: the app's header from the app's own origin, or nothing
        self.assertEqual(self.req("/v1/pair", {"base": "http://x.example"}, Cookie=pair)[0], 403)  # a form post
        self.assertEqual(self.req("/v1/pair", {"base": "http://x.example"}, Cookie=pair, Origin="https://evil.example",
                                  **{"X-Libra-CSRF": "1"})[0], 403)
        host = self.base.split("//")[1]
        self.assertEqual(self.req("/v1/pair", {"base": "http://x.example"}, Cookie=pair, Origin=f"http://{host}",
                                  **{"X-Libra-CSRF": "1"})[0], 200)
        # bearer clients aren't affected
        self.assertEqual(self.req("/v1/pair", {"base": "http://x.example"}, Authorization="Bearer main-token")[0], 200)

    def test_sessions_end_when_they_should(self):
        from libra import devices

        pair, _, _ = self.sign_in()
        csrf = {"X-Libra-CSRF": "1"}
        self.assertEqual(self.req("/v1/session", {}, Cookie=pair, **csrf)[0], 400)  # a session can't mint sessions
        status, h, _ = self.req("/v1/session/logout", {}, Cookie=pair, **csrf)
        self.assertEqual(status, 200)
        self.assertIn("Max-Age=0", h["Set-Cookie"])
        status, h, _ = self.req("/v1/info", Cookie=pair)
        self.assertEqual(status, 401)  # signed out on the server, not just in the browser
        self.assertIn("Max-Age=0", h["Set-Cookie"])  # and the stale cookie is cleared

        # a device's session dies with the device
        dev_token, rec = devices.create("phone")
        dpair, _, out = self.sign_in(dev_token)
        self.assertEqual(out["principal"], f"device:{rec['id']}")
        self.assertTrue(self.req("/v1/devices", Cookie=dpair)[2]["devices"][0]["current"])
        devices.revoke(rec["id"])
        self.assertEqual(self.req("/v1/info", Cookie=dpair)[0], 401)

        # an owner session dies when the main token changes
        pair, _, _ = self.sign_in()
        other = self.serve("rotated-token")  # same ~/.libra, new main token
        self.assertEqual(self.req("/v1/info", Cookie=pair, base=other)[0], 401)
        self.assertEqual(self.req("/v1/info", Cookie=pair)[0], 200)

    def test_only_session_ids_are_accepted_from_cookies(self):
        from libra import devices

        dev_token, _ = devices.create("phone")
        for value in ("main-token", dev_token, "ses_madeup", "x" * 40):
            self.assertEqual(self.req("/v1/info", Cookie=f"libra_session={value}")[0], 401, value)
        self.assertEqual(self.req("/v1/info", Cookie="garbage;;=\x7f")[0], 401)
        self.assertEqual(self.req("/v1/session", {}, Authorization="Bearer wrong")[0], 401)

    def test_without_tokens_there_is_no_session(self):
        base = self.serve(None)
        status, h, out = self.req("/v1/session", {}, base=base)
        self.assertEqual((status, out["session"]), (200, False))
        self.assertIsNone(h.get("Set-Cookie"))


if __name__ == "__main__":
    unittest.main()
