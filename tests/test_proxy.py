"""Proxies and the private-address guard (fifth review, P0). With HTTP(S)_PROXY set, the proxy makes the
connection, so a name only the proxy can resolve (an internal host) could slip past a check done with local
DNS. Environment proxies are therefore ignored for fetches unless LIBRA_USE_PROXY=1 says the proxy is
trusted, and even then a name must resolve locally to public addresses. Every other downloader goes through
the same guarded fetcher."""
import os
import socket
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock


class FakeProxy:
    """An HTTP proxy that records every request it receives and answers with a 'secret'."""

    def __init__(self):
        seen = self.seen = []

        class H(BaseHTTPRequestHandler):
            def do_GET(self):
                seen.append(self.path)  # a proxied request carries the full URL
                body = b"internal secret"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self):
        self.server.shutdown()
        self.server.server_close()


class ProxyTest(unittest.TestCase):
    def setUp(self):
        self.proxy = FakeProxy()
        clean = {k: v for k, v in os.environ.items() if k.lower() not in ("http_proxy", "https_proxy", "no_proxy",
                                                                          "all_proxy", "libra_use_proxy")}
        self.env = mock.patch.dict(os.environ, {**clean, "http_proxy": self.proxy.url, "HTTP_PROXY": self.proxy.url},
                                   clear=True)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.proxy.close()

    def fetcher(self):
        from libra.web import Fetcher

        return Fetcher(respect_robots=False)

    def test_environment_proxy_is_not_used_by_default(self):
        from libra.web import FetchError

        with self.assertRaises(FetchError):
            self.fetcher().get("http://intranet.libra-test.invalid/")  # only a proxy could resolve this
        self.assertEqual(self.proxy.seen, [], "the request went through the proxy")

    def test_a_trusted_proxy_still_needs_a_public_name(self):
        from libra.web import FetchError

        real = socket.getaddrinfo

        def fake_dns(host, *a, **k):
            if host == "public.libra-test.example":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))]
            if host == "sneaky.libra-test.example":
                return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 80))]
            return real(host, *a, **k)

        with mock.patch.dict(os.environ, {"LIBRA_USE_PROXY": "1"}), mock.patch("socket.getaddrinfo", fake_dns):
            with self.assertRaises(FetchError):  # can't be resolved here: refused, not handed to the proxy
                self.fetcher().get("http://intranet.libra-test.invalid/")
            with self.assertRaises(FetchError):  # resolves to a private address
                self.fetcher().get("http://sneaky.libra-test.example/")
            self.assertEqual(self.proxy.seen, [])
            r = self.fetcher().get("http://public.libra-test.example/page")  # public: the trusted proxy is used
            self.assertEqual(r.text, "internal secret")
            self.assertEqual(self.proxy.seen, ["http://public.libra-test.example/page"])

    def test_dataset_downloads_use_the_guard(self):
        from libra import dataset
        from libra.web import FetchError

        with self.assertRaises(FetchError):
            dataset.fetch("http://127.0.0.1:9/notes.txt")
        with self.assertRaises(FetchError):
            dataset.fetch("http://intranet.libra-test.invalid/notes.txt")
        self.assertEqual(self.proxy.seen, [])


if __name__ == "__main__":
    unittest.main()
