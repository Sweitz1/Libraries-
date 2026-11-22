"""The codex website + MediaWiki-compatible api.php: spin the real server and make real HTTP requests,
including pulling from it with worldbook (the whole point — your own wiki read like a Fandom wiki)."""
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from urllib.parse import quote, urlencode


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get(url):
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.status, r.read()


class SiteTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = os.path.join(self.tmp.name, "home")
        from libra import codex, codexsite

        self.codex = codex
        codex.save("USS Daedalus", kind="ship", fields={"class": "BC-304", "crew": "200"},
                   body="Earth's second BC-304.", categories=["Ships", "Tau'ri"])
        self.srv = codexsite.make_server("127.0.0.1", 0)
        self.port = self.srv.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def test_index_lists_entries_and_form(self):
        status, body = get(self.base + "/")
        self.assertEqual(status, 200)
        self.assertIn(b"USS Daedalus", body)
        self.assertIn(b"Add / update an entry", body)

    def test_entry_page(self):
        _, body = get(self.base + "/wiki/uss-daedalus")
        self.assertIn(b"BC-304", body)
        self.assertIn(b"Ships", body)

    def test_entry_page_is_fandom_styled(self):
        self.codex.save("Thor", kind="character", fields={"race": "Asgard"},
                        body="The '''Thor''' is an Asgard.\n== Role ==\nSupreme Commander.",
                        categories=["Asgard"])
        _, body = get(self.base + "/wiki/thor")
        text = body.decode()
        self.assertIn('class="portable-infobox"', text)        # Fandom infobox classes
        self.assertIn('pi-data-label', text)
        self.assertIn('pi-data-value', text)
        self.assertIn('<h2 class="sec">Role</h2>', text)       # == Role == became a section
        self.assertIn("<b>Thor</b>", text)                     # '''bold''' rendered

    def test_api_parse_is_mediawiki_shaped(self):
        status, body = get(self.base + "/api.php?" + urlencode({"action": "parse", "page": "USS Daedalus",
                                                                "format": "json"}))
        data = json.loads(body)
        wt = data["parse"]["wikitext"]
        self.assertIn("{{Infobox ship", wt)
        self.assertIn("| class = BC-304", wt)
        # the real reader parses it back into fields
        from libra import wiki

        self.assertEqual(wiki.infobox_fields(wt)[1]["class"], "BC-304")
        self.assertEqual(data["parse"]["categories"][0]["category"], "Ships")

    def test_api_search(self):
        _, body = get(self.base + "/api.php?" + urlencode({"action": "query", "list": "search",
                                                           "srsearch": "BC-304", "format": "json"}))
        titles = [row["title"] for row in json.loads(body)["query"]["search"]]
        self.assertIn("USS Daedalus", titles)

    def test_post_add_urlencoded(self):
        data = urlencode({"title": "Asgard Beam", "kind": "tech", "fields": "type = energy weapon",
                          "body": "A devastating weapon.", "categories": "Weapons"}).encode()
        req = urllib.request.Request(self.base + "/add", data=data)
        with urllib.request.urlopen(req, timeout=5) as r:
            self.assertEqual(r.status, 200)                    # followed the 303 redirect to the entry page
            self.assertIn(b"Asgard Beam", r.read())
        self.assertEqual(self.codex.get("Asgard Beam")["fields"]["type"], "energy weapon")

    def test_post_add_multipart_with_image(self):
        from libra.raster import Canvas

        png = Canvas(4, 4, (0, 0, 0)).to_png()
        b = "----libratest"
        parts = []
        for name, val in (("title", "Shielded Ship"), ("kind", "ship"), ("fields", "shields = high")):
            parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{val}\r\n".encode())
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"a.png\"\r\n"
                     f"Content-Type: image/png\r\n\r\n".encode() + png + b"\r\n")
        parts.append(f"--{b}--\r\n".encode())
        body = b"".join(parts)
        req = urllib.request.Request(self.base + "/add", data=body,
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        entry = self.codex.get("Shielded Ship")
        self.assertEqual(entry["image"], "images/shielded-ship.png")
        status, served = get(self.base + "/images/shielded-ship.png")
        self.assertEqual(served[:8], b"\x89PNG\r\n\x1a\n")     # the uploaded image is served back

    def test_worldbook_pulls_from_the_local_codex_like_fandom(self):
        # the payoff: point Libra's wiki reader at your own codex
        os.environ["LIBRA_ALLOW_PRIVATE"] = "1"                # fetcher's SSRF guard blocks localhost otherwise
        try:
            spec = importlib.util.spec_from_file_location("lorebook_p", os.path.join(ROOT, "plugins", "lorebook.py"))
            lb = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(lb)
            r = lb.worldbook(self.base + "/wiki/USS_Daedalus")
            self.assertTrue(r.ok, r.text)
            self.assertEqual(r.data["fields"]["class"], "BC-304")
            self.assertEqual(r.data["source"], "mediawiki-api")
            # and world_build can search + pull from it too
            w = lb.world_build(f"{self.base}=Daedalus")
            self.assertTrue(w.ok, w.text)
            self.assertTrue(any(e["title"] == "USS Daedalus" for e in w.data["world"]))
        finally:
            os.environ.pop("LIBRA_ALLOW_PRIVATE", None)


if __name__ == "__main__":
    unittest.main()
