"""The codex website + MediaWiki api.php (continuity model). Spins the real server, makes real requests,
and checks worldbook can read the canon continuity like a Fandom wiki."""
import importlib.util
import json
import os
import tempfile
import threading
import unittest
import urllib.request
from urllib.parse import urlencode

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

    def test_index_has_continuity_tabs_and_form(self):
        _, body = get(self.base + "/")
        text = body.decode()
        self.assertIn("Main Canon", text)
        self.assertIn('class="conts"', text)                   # continuity tabs
        self.assertIn("Add / update an entry", text)
        self.assertIn("USS Daedalus", text)

    def test_entry_page(self):
        _, body = get(self.base + "/wiki/main-canon/uss-daedalus")
        text = body.decode()
        self.assertIn("BC-304", text)
        self.assertIn("Ships", text)
        self.assertIn('class="portable-infobox"', text)
        _, canon = get(self.base + "/wiki/uss-daedalus")       # single segment resolves to canon
        self.assertIn("BC-304", canon.decode())

    def test_au_entry_kept_separate_with_cross_link(self):
        self.codex.add_continuity("Frostline AU", "au")
        self.codex.save("USS Daedalus", type="tech", fields={"class": "Prototype"}, continuity="Frostline AU")
        _, body = get(self.base + "/wiki/frostline-au/uss-daedalus")
        text = body.decode()
        self.assertIn("Prototype", text)
        self.assertIn("Also in other continuities", text)      # auto cross-world link to the canon version
        self.assertIn("Frostline AU", text)

    def test_api_parse_and_search_read_the_canon(self):
        _, body = get(self.base + "/api.php?" + urlencode({"action": "parse", "page": "USS Daedalus",
                                                           "format": "json"}))
        wt = json.loads(body)["parse"]["wikitext"]
        self.assertIn("{{Infobox ship", wt)                    # the entry's free kind names the template
        self.assertIn("| class = BC-304", wt)
        from libra import wiki

        self.assertEqual(wiki.infobox_fields(wt)[1]["class"], "BC-304")
        _, s = get(self.base + "/api.php?" + urlencode({"action": "query", "list": "search",
                                                       "srsearch": "BC-304", "format": "json"}))
        self.assertIn("USS Daedalus", [r["title"] for r in json.loads(s)["query"]["search"]])

    def test_post_add_with_type_and_continuity(self):
        data = urlencode({"title": "Thor", "type": "character", "continuity": "main-canon",
                          "fields": "race = Asgard", "body": "A commander.", "date": "2004"}).encode()
        with urllib.request.urlopen(urllib.request.Request(self.base + "/add", data=data), timeout=5) as r:
            self.assertIn(b"Thor", r.read())
        e = self.codex.get("Thor")
        self.assertEqual(e["type"], "character")
        self.assertEqual(e["date"], "2004")

    def test_post_add_multipart_image(self):
        from libra.raster import Canvas

        png = Canvas(4, 4, (0, 0, 0)).to_png()
        b = "----libratest"
        parts = [f"--{b}\r\nContent-Disposition: form-data; name=\"{n}\"\r\n\r\n{v}\r\n".encode()
                 for n, v in (("title", "Shielded Ship"), ("type", "tech"), ("continuity", "main-canon"))]
        parts.append(f"--{b}\r\nContent-Disposition: form-data; name=\"image\"; filename=\"a.png\"\r\n"
                     f"Content-Type: image/png\r\n\r\n".encode() + png + b"\r\n" + f"--{b}--\r\n".encode())
        req = urllib.request.Request(self.base + "/add", data=b"".join(parts),
                                     headers={"Content-Type": f"multipart/form-data; boundary={b}"})
        with urllib.request.urlopen(req, timeout=5) as r:
            r.read()
        self.assertEqual(self.codex.get("Shielded Ship")["image"], "images/main-canon/shielded-ship.png")
        _, served = get(self.base + "/images/main-canon/shielded-ship.png")
        self.assertEqual(served[:8], b"\x89PNG\r\n\x1a\n")

    def test_worldbook_reads_the_codex(self):
        os.environ["LIBRA_ALLOW_PRIVATE"] = "1"
        try:
            spec = importlib.util.spec_from_file_location("lorebook_p", os.path.join(ROOT, "plugins", "lorebook.py"))
            lb = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(lb)
            r = lb.worldbook(self.base + "/wiki/USS_Daedalus")
            self.assertTrue(r.ok, r.text)
            self.assertEqual(r.data["fields"]["class"], "BC-304")
            w = lb.world_build(f"{self.base}=Daedalus")
            self.assertTrue(w.ok, w.text)
            self.assertTrue(any(e["title"] == "USS Daedalus" for e in w.data["world"]))
        finally:
            os.environ.pop("LIBRA_ALLOW_PRIVATE", None)


if __name__ == "__main__":
    unittest.main()
