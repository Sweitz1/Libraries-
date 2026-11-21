"""SSRF protections: DNS rebinding, redirects to internal names, robots.txt, special address forms."""
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

import libra.web as web


class Internal(BaseHTTPRequestHandler):
    hits = []

    def log_message(self, *a):
        pass

    def do_GET(self):
        Internal.hits.append((self.headers.get("Host"), self.path))
        if self.path == "/go-inside":
            self.send_response(302)
            self.send_header("Location", f"http://internal.test:{self.server.server_address[1]}/secret")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        body = b"<html><body><main><p>internal secret</p></main></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class SSRFTest(unittest.TestCase):
    def setUp(self):
        Internal.hits = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Internal)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.real_getaddrinfo = socket.getaddrinfo
        self.patches = [mock.patch.object(web, "_uses_proxy", return_value=False)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.server.shutdown()
        self.server.server_close()

    def fake_dns(self, answers):
        """getaddrinfo that answers names from ``answers`` (a list per name is used in order)."""
        calls = {}

        def getaddrinfo(host, port, *args, **kwargs):
            if host in answers:
                seq = answers[host]
                i = calls.get(host, 0)
                calls[host] = i + 1
                ip = seq[min(i, len(seq) - 1)]
                return self.real_getaddrinfo(ip, port, *args, **kwargs)
            return self.real_getaddrinfo(host, port, *args, **kwargs)

        return mock.patch("socket.getaddrinfo", side_effect=getaddrinfo)

    def test_dns_rebinding_is_refused(self):
        # first answer public (passes the name check), every later answer is this machine
        with self.fake_dns({"rebind.test": ["93.184.216.34", "127.0.0.1"]}):
            with self.assertRaises(web.FetchError) as cm:
                web.Fetcher(respect_robots=False).request("GET", f"http://rebind.test:{self.port}/secret")
        self.assertIn("refusing private or local address", str(cm.exception))
        self.assertEqual(Internal.hits, [])  # the internal server was never reached

    def test_robots_fetch_is_checked_too(self):
        with self.fake_dns({"rebind.test": ["93.184.216.34", "127.0.0.1"]}):
            f = web.Fetcher(respect_robots=True)
            with self.assertRaises(web.FetchError):
                f.get(f"http://rebind.test:{self.port}/page")
        self.assertEqual(Internal.hits, [])  # neither robots.txt nor the page reached it

    def test_redirect_to_internal_name(self):
        # treat 127.0.0.1 as "public" and 127.0.0.2 as internal, so the redirect hop is the one refused
        blocked = lambda ip: ip == "127.0.0.2"  # noqa: E731
        with mock.patch.object(web, "_blocked_ip", side_effect=blocked), \
                self.fake_dns({"pub.test": ["127.0.0.1"], "internal.test": ["127.0.0.2"]}):
            with self.assertRaises(web.FetchError):
                web.Fetcher(respect_robots=False).request("GET", f"http://pub.test:{self.port}/go-inside")
        self.assertEqual([p for _, p in Internal.hits], ["/go-inside"])  # the redirect target was never fetched

    def test_allow_private_still_works(self):
        r = web.Fetcher(respect_robots=False, allow_private=True).request("GET", f"http://127.0.0.1:{self.port}/x")
        self.assertIn("internal secret", r.text)

    def test_special_addresses(self):
        for ip in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "169.254.169.254", "100.101.102.103", "0.0.0.0",
                   "::1", "::ffff:127.0.0.1", "::ffff:10.0.0.1", "fe80::1", "fc00::1", "224.0.0.1"):
            self.assertTrue(web._blocked_ip(ip), ip)
        for ip in ("93.184.216.34", "8.8.8.8", "2606:4700:4700::1111"):
            self.assertFalse(web._blocked_ip(ip), ip)


if __name__ == "__main__":
    unittest.main()
