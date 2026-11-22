"""The lorebook plugin: structured character/world/spec extraction from fan wikis and fan sites.

No network is used — the guarded fetcher is replaced with a fake that returns crafted MediaWiki API JSON
or HTML. The tests check the wikitext infobox parsing, the lead summary, the HTML infobox fallback, the
"not a wiki" path, and that a hub page with nothing structured is reported rather than dumped."""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("lorebook_plugin", os.path.join(ROOT, "plugins", "lorebook.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Resp:
    def __init__(self, text, status=200, content_type="text/html"):
        self.text = text
        self.status = status
        self.content_type = content_type

    @property
    def is_html(self):
        return "html" in self.content_type or self.text.lstrip()[:15].lower().startswith(("<!doctype", "<html"))


def api_json(wikitext, categories=(), title="Daedalus"):
    return json.dumps({"parse": {"title": title, "displaytitle": title, "wikitext": wikitext,
                                 "categories": [{"category": c} for c in categories]}})


DAEDALUS_WIKITEXT = (
    "{{Infobox starship\n"
    "| name = Daedalus\n"
    "| image = Daedalus.jpg\n"
    "| class = [[BC-304]]\n"
    "| affiliation = [[United States|Tau'ri]]\n"
    "| armament = [[Railgun]]s, [[Missile|missiles]], [[Asgard beam weapon]]s\n"
    "| commander = [[Steven Caldwell]]\n"
    "}}\n"
    "The '''Daedalus''' is a [[BC-304|''Daedalus''-class]] battlecruiser operated by the Tau'ri. It was "
    "the second human-built interstellar warship.\n\n"
    "== History ==\nIt first launched to defend Atlantis.\n")


class WikiPathTest(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def run_skill(self, url, response_text, **kw):
        fake = mock.Mock()
        fake.get = mock.Mock(return_value=Resp(response_text, content_type="application/json", **kw))
        with mock.patch.object(self.m, "fetcher", return_value=fake):
            return self.m.worldbook(url), fake

    def test_fandom_infobox_becomes_structured_fields(self):
        res, fake = self.run_skill(
            "https://stargate.fandom.com/wiki/Daedalus",
            api_json(DAEDALUS_WIKITEXT, categories=["BC-304", "Tau'ri starships", "Hidden category"]))
        self.assertTrue(res.ok)
        d = res.data
        self.assertEqual(d["title"], "Daedalus")
        self.assertEqual(d["kind"], "ship")                 # from "starship"/"starships"
        self.assertEqual(d["source"], "mediawiki-api")
        # wiki markup is stripped to plain spec values
        self.assertEqual(d["fields"]["class"], "BC-304")
        self.assertEqual(d["fields"]["affiliation"], "Tau'ri")        # [[United States|Tau'ri]] -> Tau'ri
        self.assertEqual(d["fields"]["commander"], "Steven Caldwell")
        self.assertIn("Railgun", d["fields"]["armament"])
        self.assertNotIn("[[", d["fields"]["armament"])
        # image/layout fields are dropped, not shown as lore
        self.assertNotIn("image", d["fields"])
        # summary is the lead prose, not a lore drop of the whole article
        self.assertIn("battlecruiser", d["summary"])
        self.assertNotIn("== History", d["summary"])
        self.assertNotIn("first launched to defend", d["summary"])
        self.assertIn("BC-304", d["categories"])
        self.assertNotIn("Hidden category", d["categories"])   # hidden cats filtered
        # the API was asked, with the de-underscored title
        self.assertIn("action=parse", fake.get.call_args[0][0])

    def test_card_text_is_a_readable_lorebook_card(self):
        res, _ = self.run_skill("https://stargate.fandom.com/wiki/Daedalus", api_json(DAEDALUS_WIKITEXT))
        self.assertIn("# Daedalus", res.text)
        self.assertIn("## Spec data", res.text)
        self.assertIn("**class:** BC-304", res.text)
        self.assertIn("Source:", res.text)

    def test_writes_json_when_out_given(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "ship.json")
            fake = mock.Mock(get=mock.Mock(return_value=Resp(api_json(DAEDALUS_WIKITEXT),
                                                             content_type="application/json")))
            with mock.patch.object(self.m, "fetcher", return_value=fake):
                res = self.m.worldbook("https://stargate.fandom.com/wiki/Daedalus", out=out)
            self.assertTrue(res.ok)
            with open(out, encoding="utf-8") as f:
                saved = json.load(f)
            self.assertEqual(saved["fields"]["class"], "BC-304")


HTML_INFOBOX = """<!doctype html><html><head><title>Atlantis - GateWorld</title></head><body>
<article>
<h1>Atlantis</h1>
<aside class="portable-infobox">
  <h2 class="pi-title">Atlantis</h2>
  <div class="pi-item pi-data"><h3 class="pi-data-label">Type</h3><div class="pi-data-value">City-ship</div></div>
  <div class="pi-item pi-data"><h3 class="pi-data-label">Builders</h3><div class="pi-data-value">The Ancients</div></div>
  <div class="pi-item pi-data"><h3 class="pi-data-label">Image</h3><div class="pi-data-value">atlantis.png</div></div>
</aside>
<p>Atlantis is a city-ship built by the Ancients millions of years ago and later settled on a planet in the
Pegasus galaxy, where it became the base of the Atlantis expedition.</p>
<p>Short.</p>
</article></body></html>"""


class HtmlPathTest(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def run_html(self, url, text):
        fake = mock.Mock()
        # wiki path fails (API returns the HTML, not parse JSON), then the HTML path runs on the same text
        fake.get = mock.Mock(return_value=Resp(text, content_type="text/html"))
        with mock.patch.object(self.m, "fetcher", return_value=fake):
            return self.m.worldbook(url)

    def test_generic_site_portable_infobox(self):
        res = self.run_html("https://www.gateworld.net/atlantis/", HTML_INFOBOX)
        self.assertTrue(res.ok)
        d = res.data
        self.assertEqual(d["source"], "html")
        self.assertEqual(d["fields"]["Type"], "City-ship")
        self.assertEqual(d["fields"]["Builders"], "The Ancients")
        self.assertNotIn("Image", d["fields"])              # image value dropped
        self.assertIn("city-ship built by the Ancients", d["summary"])

    def test_non_wiki_url_still_scrapes_html(self):
        # a URL with no /wiki/ path never tries the API; it goes straight to HTML
        res = self.run_html("https://example.org/page.html", HTML_INFOBOX)
        self.assertTrue(res.ok)
        self.assertEqual(res.data["fields"]["Type"], "City-ship")

    def test_hub_page_with_nothing_structured_is_reported(self):
        res = self.run_html("https://example.org/index.html",
                            "<html><body><nav>menu</nav><ul><li>a</li><li>b</li></ul></body></html>")
        self.assertFalse(res.ok)
        self.assertIn("No lore found", res.text)


class UnitTest(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_strip_wiki_handles_links_refs_and_templates(self):
        v = self.m._strip_wiki("[[A|B]] and [[C]]{{convert|10|m}}<ref>note</ref> '''bold'''")
        self.assertNotIn("[[", v)
        self.assertNotIn("ref", v)
        self.assertNotIn("convert", v)
        self.assertIn("B", v)
        self.assertIn("bold", v)

    def test_split_params_ignores_pipes_inside_links_and_templates(self):
        parts = self.m._split_params("Infobox| a = [[x|y]] | b = {{t|1|2}} | c = plain")
        self.assertEqual(len(parts), 4)                     # name + 3 params, not split on inner pipes

    def test_wiki_targets_builds_api_url_and_title(self):
        targets = list(self.m._wiki_targets("https://stargate.fandom.com/wiki/USS_Daedalus"))
        api, title = targets[0]
        self.assertEqual(api, "https://stargate.fandom.com/api.php")
        self.assertEqual(title, "USS Daedalus")

    def test_guess_kind(self):
        self.assertEqual(self.m._guess_kind("Infobox character", []), "character")
        self.assertEqual(self.m._guess_kind("Infobox", ["Planets"]), "location")
        self.assertEqual(self.m._guess_kind("Infobox", []), "entry")


class RegistrationTest(unittest.TestCase):
    def test_registration_and_outside_content(self):
        from libra.skills import SkillRegistry

        reg = SkillRegistry()
        got = reg.load_plugins([os.path.join(ROOT, "plugins")], log=None)
        self.assertIn("worldbook", got)
        sk = reg.get("worldbook")
        self.assertTrue(sk.untrusted_output)                # web content is marked outside
        # unaccepted plugin: its read label is held as write until the file is accepted
        self.assertEqual((sk.risk, sk.claimed_risk), ("write", "read"))


if __name__ == "__main__":
    unittest.main()
