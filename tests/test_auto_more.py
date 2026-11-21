"""Auto-training: discovery of feeds/sitemaps, sitemap sources, retries, progress and notifications."""
import json
import os
import random
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

WORDS = "the river runs past the old mill where children learn to read and write stories in the quiet town".split()


def para(seed):
    rnd = random.Random(seed)
    return " ".join(" ".join(rnd.choice(WORDS) for _ in range(14)).capitalize() + "." for _ in range(8))


class Site(BaseHTTPRequestHandler):
    hits: dict = {}
    posts: list = []

    def log_message(self, *a):
        pass

    def send(self, status, body, ctype="text/html"):
        data = body.encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        base = f"http://{self.headers['Host']}"
        Site.hits[self.path] = Site.hits.get(self.path, 0) + 1
        if self.path == "/robots.txt":
            return self.send(200, f"User-agent: *\nDisallow: /private/\nSitemap: {base}/sitemap-index.xml\n", "text/plain")
        if self.path == "/":
            return self.send(200, f'<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml">'
                                  f'</head><body><main><p>{para(0)}</p><a href="/p1">one</a></main></body></html>')
        if self.path == "/sitemap-index.xml":
            return self.send(200, f'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap>'
                                  f'<loc>{base}/sitemap-pages.xml</loc></sitemap></sitemapindex>', "application/xml")
        if self.path == "/sitemap-pages.xml":
            urls = "".join(f"<url><loc>{base}/p{i}</loc><lastmod>2026-01-{i:02d}</lastmod></url>" for i in range(1, 6))
            urls += f"<url><loc>{base}/flaky</loc></url><url><loc>{base}/gone</loc></url>"
            urls += f"<url><loc>{base}/private/x</loc></url><url><loc>http://elsewhere.invalid/p</loc></url>"
            return self.send(200, f'<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>',
                             "application/xml")
        if self.path == "/feed.xml":
            return self.send(200, f'<rss><channel><item><link>{base}/news</link></item></channel></rss>', "application/xml")
        if self.path == "/flaky":
            if Site.hits[self.path] <= 2:
                return self.send(503, "busy")
            return self.send(200, f"<html><body><main><p>{para(77)}</p></main></body></html>")
        if self.path == "/gone":
            return self.send(404, "no")
        if self.path in ("/news",) or self.path.startswith("/p"):
            return self.send(200, f"<html><head><title>{self.path}</title></head><body><main><p>{para(self.path)}</p>"
                                  f"</main></body></html>")
        return self.send(404, "no")

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        Site.posts.append({"path": self.path, "title": self.headers.get("Title"), "body": self.rfile.read(n).decode()})
        self.send(200, "ok", "text/plain")


class AutoMoreTest(unittest.TestCase):
    def setUp(self):
        Site.hits, Site.posts = {}, []
        self.tmp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Site)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        from libra.auto import TEMPLATE

        cfg = json.loads(json.dumps(TEMPLATE))
        cfg.update(sources=[{"crawl": self.base + "/", "max_pages": 20, "depth": 0}], allow_private=True,
                   steps_per_run=10, device="cpu", notify={"url": self.base + "/hook", "on": "always"})
        cfg["limits"].update(delay_seconds=0, min_free_disk_mb=1, retry_delay_seconds=0)
        cfg["model_config"].update(layers=1, embd=32, heads=2, ctx=32, vocab=400, batch=8)
        cfg["filters"]["min_chars"] = 200
        cfg["gate"]["holdout_fraction"] = 0.25
        self.cfg_path = os.path.join(self.tmp.name, "auto.json")
        with open(self.cfg_path, "w") as f:
            json.dump(cfg, f)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def test_discovery_sitemaps_retries_progress_notify(self):
        from libra import auto

        r = auto.run(self.cfg_path, log=None)
        self.assertEqual(sorted(r.discovered), sorted([self.base + "/feed.xml", self.base + "/sitemap-index.xml"]))
        self.assertEqual(Site.hits["/flaky"], 3)  # two 503s retried, then it worked
        self.assertEqual(r.retried, 2)
        self.assertEqual(Site.hits.get("/gone"), 1)  # a 404 is not retried
        self.assertNotIn("/private/x", Site.hits)  # robots.txt respected for sitemap entries too
        self.assertEqual(Site.hits.get("/p1"), 1)  # linked from the page and in the sitemap: fetched once
        self.assertEqual(Site.hits.get("/news"), 1)  # from the discovered feed
        self.assertEqual(r.kept, 8)  # home, p1..p5, flaky, news
        prog = auto.progress(self.cfg_path)
        self.assertEqual(prog["phase"], "done")
        self.assertEqual(prog["kept"], 8)
        self.assertGreater(prog["steps"], 0)
        self.assertFalse(auto.is_running(self.cfg_path))
        self.assertEqual(len(Site.posts), 1)  # notification sent
        self.assertTrue(Site.posts[0]["title"].startswith("Libra my-model: kept the new model"))
        self.assertIn("found new sources", Site.posts[0]["body"])

        robots_before = Site.hits["/robots.txt"]
        r2 = auto.run(self.cfg_path, log=None)  # discovery is remembered, not repeated every run
        self.assertEqual(r2.discovered, [])
        self.assertLessEqual(Site.hits["/robots.txt"] - robots_before, 1)  # only the crawler's robots.txt check

    def test_notify_only_on_changes(self):
        from libra import auto

        with open(self.cfg_path) as f:
            cfg = json.load(f)
        cfg["notify"]["on"] = "changes"
        cfg["sources"] = [{"url": self.base + "/p1"}]
        with open(self.cfg_path, "w") as f:
            json.dump(cfg, f)
        auto.run(self.cfg_path, log=None)  # not enough data to train: nothing changed, no failure
        self.assertEqual(Site.posts, [])
        cfg["notify"]["on"] = "failures"
        cfg["limits"]["min_free_disk_mb"] = 10 ** 12  # force a failure
        with open(self.cfg_path, "w") as f:
            json.dump(cfg, f)
        with self.assertRaises(RuntimeError):
            auto.run(self.cfg_path, log=None)
        self.assertEqual(len(Site.posts), 1)
        self.assertIn("run failed", Site.posts[0]["title"])


if __name__ == "__main__":
    unittest.main()
