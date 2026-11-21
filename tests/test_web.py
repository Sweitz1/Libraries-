import functools
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from libra import autograd as ag
from libra.nn import GPT, GPTConfig
from libra.server import Engine, make_server
from libra.skills import SkillRegistry, default_registry
from libra.tokenizer import BPETokenizer
from libra.web import FetchError, Fetcher, crawl, extract, normalize_url, parse_html, scrape, to_markdown

PAGES = {
    "index.html": """<!doctype html><html lang="en"><head><title>Home</title>
        <meta name="description" content="Test site"><script>var x = 1;</script>
        <script type="application/ld+json">{"@type": "Organization", "name": "Test"}</script></head>
        <body><nav><a href="/menu.html">Menu</a></nav>
        <main><h1>Welcome</h1><p class="intro lead">Hello <b>world</b> &amp; friends.</p>
        <ul id="items"><li class="item" data-price="3">Apple<li class="item sale" data-price="2">Pear</ul>
        <table><tr><th>Name</th><th>Qty</th></tr><tr><td>Apple</td><td>5</td></tr></table>
        <a href="about.html#team">About us</a> <a href="/private/secret.html">Secret</a>
        <a href="/logo.png">Logo</a> <a href="https://elsewhere.invalid/">Elsewhere</a>
        <a href="/copy.html">Copy</a> <img src="/logo.png" alt="Logo"></main></body></html>""",
    "about.html": "<html><head><title>About</title></head><body><article><h2>Team</h2><p>We build things.</p>"
                  "<a href='deep.html'>Deeper</a><a href='/'>Home</a></article></body></html>",
    "deep.html": "<html><head><title>Deep</title></head><body><p>Two levels down.</p></body></html>",
    "copy.html": "<html><head><title>About</title></head><body><article><h2>Team</h2><p>We build things.</p>"
                 "<a href='deep.html'>Deeper</a><a href='/'>Home</a></article></body></html>",
    "menu.html": "<html><body><p>Menu page</p></body></html>",
    "private/secret.html": "<html><body><p>Do not crawl</p></body></html>",
    "robots.txt": "User-agent: *\nDisallow: /private/\n",
}


class LocalSite:
    def __enter__(self):
        self.dir = tempfile.TemporaryDirectory()
        for name, body in PAGES.items():
            path = os.path.join(self.dir.name, name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w") as f:
                f.write(body)
        handler = functools.partial(QuietHandler, directory=self.dir.name)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/"
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.dir.cleanup()


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


class SelectorTest(unittest.TestCase):
    def setUp(self):
        self.doc = parse_html(PAGES["index.html"])

    def texts(self, sel):
        return [n.text for n in self.doc.select(sel)]

    def test_selectors(self):
        self.assertEqual(self.texts("li.item"), ["Apple", "Pear"])
        self.assertEqual(self.texts("#items > .sale"), ["Pear"])
        self.assertEqual(self.texts("main p.intro.lead b"), ["world"])
        self.assertEqual(self.texts("[data-price='2']"), ["Pear"])
        self.assertEqual(self.texts("a[href^='/private']"), ["Secret"])
        self.assertEqual(self.texts("a[href$='.png']"), ["Logo"])
        self.assertEqual(self.texts("li:first-child, li:nth-child(2)"), ["Apple", "Pear"])
        self.assertEqual(self.texts("h1, td:last-child"), ["Welcome", "5"])
        self.assertEqual(self.texts("nav > a"), ["Menu"])
        self.assertEqual(self.texts("body > p"), [])
        with self.assertRaises(ValueError):
            self.doc.select("li:hover")

    def test_extract(self):
        data = extract(PAGES["index.html"], "http://site.test/")
        self.assertEqual(data["title"], "Home")
        self.assertEqual(data["description"], "Test site")
        self.assertEqual(data["lang"], "en")
        self.assertEqual(data["tables"][0], [["Name", "Qty"], ["Apple", "5"]])
        self.assertEqual(data["json_ld"][0]["name"], "Test")
        self.assertIn("Hello world & friends.", data["text"])
        self.assertNotIn("var x", data["text"])
        self.assertNotIn("Menu", data["text"])  # navigation removed
        self.assertIn({"url": "http://site.test/about.html", "text": "About us"}, data["links"])
        self.assertEqual(data["headings"], [{"level": 1, "text": "Welcome"}])
        self.assertIn("| Name | Qty |", to_markdown(data))

    def test_normalize(self):
        self.assertEqual(normalize_url("about.html#x", "HTTP://Site.TEST/a/b"), "http://site.test/a/about.html")
        self.assertIsNone(normalize_url("mailto:me@example.com"))
        self.assertIsNone(normalize_url("javascript:void(0)"))


class FetchCrawlTest(unittest.TestCase):
    def test_private_addresses_refused_by_default(self):
        with LocalSite() as site:
            with self.assertRaises(FetchError):
                Fetcher().get(site.url)

    def test_scrape_and_robots(self):
        with LocalSite() as site:
            f = Fetcher(allow_private=True)
            page = scrape(site.url, ["li.item"], f)
            self.assertEqual(page["selected"]["li.item"], ["Apple", "Pear"])
            with self.assertRaises(FetchError):
                f.get(site.url + "private/secret.html")
            self.assertTrue(Fetcher(allow_private=True, respect_robots=False).get(site.url + "private/secret.html"))
            with self.assertRaises(FetchError):
                f.get(site.url + "missing.html")

    def test_crawl(self):
        with LocalSite() as site:
            errors = []
            pages = list(crawl(site.url, max_pages=10, max_depth=2, delay=0,
                               fetcher=Fetcher(allow_private=True),
                               on_error=lambda u, e: errors.append(u)))
            urls = [p.url.replace(site.url, "/") for p in pages]
            self.assertEqual(urls[0], "/")
            self.assertIn("/about.html", urls)
            self.assertIn("/deep.html", urls)
            self.assertNotIn("/copy.html", urls)  # duplicate content skipped
            self.assertFalse(any("private" in u for u in urls + errors))  # robots.txt respected
            self.assertFalse(any("png" in u or "elsewhere" in u for u in urls + errors))
            shallow = list(crawl(site.url, max_pages=10, max_depth=0, fetcher=Fetcher(allow_private=True)))
            self.assertEqual(len(shallow), 1)


class SkillsTest(unittest.TestCase):
    def setUp(self):
        os.environ["LIBRA_ALLOW_PRIVATE"] = "1"
        self.reg = default_registry(log=None, load_default_dir=False)

    def tearDown(self):
        os.environ.pop("LIBRA_ALLOW_PRIVATE", None)

    def test_calc_and_routes(self):
        self.assertEqual(self.reg.run("calc", {"expression": "(2+3)*4"}).text, "(2+3)*4 = 20")
        self.assertFalse(self.reg.run("calc", {"expression": "__import__('os')"}).ok)
        self.assertEqual(self.reg.route("what is 12 * 3?"), ("calc", {"expression": "12 * 3"}))
        self.assertEqual(self.reg.route("scrape https://a.test/x."), ("scrape", {"url": "https://a.test/x"}))
        self.assertEqual(self.reg.route("crawl https://a.test"), ("crawl", {"url": "https://a.test"}))
        self.assertEqual(self.reg.route("show the links on https://a.test")[0], "links")
        self.assertEqual(self.reg.route("what time is it")[0], "time")
        self.assertIsNone(self.reg.route("tell me a story about 2024"))

    def test_parse_command_and_coercion(self):
        name, kw = self.reg.parse_command('/crawl https://a.test 5 depth=2 out="x y.jsonl"')
        self.assertEqual((name, kw), ("crawl", {"url": "https://a.test", "max_pages": "5", "depth": "2",
                                                "out": "x y.jsonl"}))
        self.assertIn("no parameter", self.reg.run("calc", {"nope": 1}).text)
        self.assertFalse(self.reg.run("missing").ok)

    def test_web_skills(self):
        with LocalSite() as site:
            res = self.reg.run("scrape", {"url": site.url, "select": "li.sale"})
            self.assertEqual(res.text, "- Pear")
            self.assertIn("# Home", self.reg.run("scrape", {"url": site.url}).text)
            with tempfile.TemporaryDirectory() as d:
                out = os.path.join(d, "pages.jsonl")
                res = self.reg.run("crawl", {"url": site.url, "max_pages": "3", "delay": "0", "out": out})
                self.assertTrue(res.ok, res.text)
                with open(out) as f:
                    self.assertEqual(len(f.readlines()), 3)
            self.assertIn("About us", self.reg.run("links", {"url": site.url, "filter": "about"}).text)

    def test_plugins(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "mine.py"), "w") as f:
                f.write("from libra.skills import skill\n"
                        "@skill('shout', 'Upper-case', router=lambda m: {'text': m[6:]} if m.startswith('shout ') else None)\n"
                        "def shout(text: str, times: int = 1):\n    return text.upper() * times\n"
                        "def register(registry):\n"
                        "    registry.skill('whisper')(lambda text: text.lower())\n")
            reg = SkillRegistry()
            self.assertEqual(sorted(reg.load_plugins([d], log=None)), ["shout", "whisper"])
            self.assertEqual(reg.run("shout", {"text": "hi", "times": "2"}).text, "HIHI")
            self.assertEqual(reg.route("shout hey"), ("shout", {"text": "hey"}))
            schema = reg.get("shout").schema()["function"]["parameters"]
            self.assertEqual(schema["properties"]["times"]["type"], "integer")
            self.assertEqual(schema["required"], ["text"])


class SkillServerTest(unittest.TestCase):
    def test_endpoints_and_auto_skills(self):
        ag.seed(0)
        tok = BPETokenizer.train("hello world " * 20, 280)
        model = GPT(GPTConfig(tok.vocab_size, 16, 1, 2, 16, 0.0)).eval()
        reg = default_registry(log=None, load_default_dir=False)
        server = make_server(Engine(model, tok, skills=reg, auto_skills=True), "127.0.0.1", 0)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            def post(path, body):
                req = urllib.request.Request(base + path, json.dumps(body).encode(),
                                             {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=20) as r:
                    return json.load(r)

            with urllib.request.urlopen(base + "/v1/skills", timeout=10) as r:
                names = [s["function"]["name"] for s in json.load(r)["data"]]
            self.assertIn("scrape", names)
            self.assertEqual(post("/v1/skills/calc", {"arguments": '{"expression": "6*7"}'})["text"], "6*7 = 42")
            res = post("/v1/chat/completions", {"messages": [{"role": "user", "content": "what is 6*7"}]})
            self.assertEqual(res["choices"][0]["message"]["content"], "6*7 = 42")
            self.assertEqual(res["libra_skill"], "calc")
            res = post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 3})
            self.assertNotIn("libra_skill", res)
        finally:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    unittest.main()
