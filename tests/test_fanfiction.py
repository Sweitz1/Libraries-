"""The fanfiction.net plugins: story metadata (fic_meta) and community story list (fic_community).

Fanfiction.net is bot-filtered and has no API, so nothing here touches the network — the guarded fetcher
is replaced with a fake returning representative page HTML. The tests check the metadata-line parser
(rating/language/genre/characters and key:value fields, with parts missing), the fandom/crossover
breadcrumb, the community row list, and that a bot-check page is reported rather than mis-parsed."""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("fanfiction_plugin", os.path.join(ROOT, "plugins", "fanfiction.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Resp:
    def __init__(self, text, status=200, content_type="text/html"):
        self.text, self.status, self.content_type = text, status, content_type

    @property
    def is_html(self):
        return True


def with_page(mod, text):
    fake = mock.Mock(get=mock.Mock(return_value=Resp(text)))
    return mock.patch.object(mod, "fetcher", return_value=fake)


STORY = """<!doctype html><html><head><title>The Long Road, a Harry Potter fanfic</title></head><body>
<div id=pre_story_links><span>
  <a class=xcontrast_txt href=/book/Harry-Potter/>Harry Potter</a></span></div>
<div id=profile_top>
  <b class=xcontrast_txt>The Long Road</b> by
  <a class=xcontrast_txt href=/u/123/Writer>Writer</a>
  <div class=xcontrast_txt style='margin-top:2px'>Harry gets a second chance and does it right. An epic retelling.</div>
  <span class='xgray xcontrast_txt'>Rated: <a href=/g/>Fiction&nbsp;T</a> - English - Adventure/Drama -
  Harry P., Hermione G. - Chapters: 40 - Words: 200,000 - Reviews: 1,234 - Favs: 5,678 - Follows: 6,000 -
  Updated: Jan 1, 2020 - Published: Jun 5, 2015 - Status: Complete - id: 11111</span>
</div></body></html>"""

CROSSOVER = """<html><body>
<div id=pre_story_links><span>
  <a class=xcontrast_txt href=/Harry-Potter-and-Naruto-Crossovers/>Harry Potter + Naruto Crossover</a></span></div>
<div id=profile_top><b class=xcontrast_txt>Worlds Collide</b> by
  <a class=xcontrast_txt href=/u/9/A>A</a>
  <div class=xcontrast_txt>Two worlds meet.</div>
  <span class='xgray xcontrast_txt'>Rated: Fiction M - English - Chapters: 12 - Words: 40,000 - id: 222</span>
</div></body></html>"""

COMMUNITY = """<html><head><title>Best Crossovers : FanFiction</title></head><body>
<div id=content_wrapper_inner>
  <span class=xcontrast_txt>Best Crossovers</span>
  <div class='z-list zhover zpointer'>
     <a class=stitle href=/s/123/1/Worlds-Collide>Worlds Collide</a> by
     <a href=/u/9/Author>Author</a>
     <div class='z-indent z-padtop'>Harry and the Enterprise crew team up to save two galaxies.
        <div class='z-padtop2 xgray'>crossover - Rated: T - English - Adventure - Words: 40,000 - id: 123</div>
     </div>
  </div>
  <div class='z-list zhover zpointer'>
     <a class=stitle href=/s/456/1/Second-Dawn>Second Dawn</a> by
     <a href=/u/8/Scribe>Scribe</a>
     <div class='z-indent z-padtop'>A Stargate and Mass Effect fusion.
        <div class='z-padtop2 xgray'>crossover - Rated: M - English - Sci-Fi/Drama - Words: 120,000 - Status: Complete - id: 456</div>
     </div>
  </div>
</div></body></html>"""

BOT_CHECK = "<html><head><title>Just a moment...</title></head><body>Enable JavaScript and cookies to continue</body></html>"


class MetaLineTest(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_full_line(self):
        d = self.m._parse_meta_line("Rated: Fiction T - English - Adventure/Drama - Harry P., Hermione G. - "
                                    "Chapters: 40 - Words: 200,000 - Status: Complete - id: 11111")
        self.assertEqual(d["rating"], "T")                         # "Fiction " stripped
        self.assertEqual(d["language"], "English")
        self.assertEqual(d["genre"], "Adventure/Drama")
        self.assertEqual(d["characters"], "Harry P., Hermione G.")
        self.assertEqual(d["chapters"], "40")
        self.assertEqual(d["words"], "200,000")
        self.assertEqual(d["status"], "Complete")
        self.assertEqual(d["id"], "11111")

    def test_missing_genre_and_characters(self):
        d = self.m._parse_meta_line("Rated: Fiction M - English - Chapters: 12 - Words: 40,000")
        self.assertEqual(d["rating"], "M")
        self.assertEqual(d["language"], "English")
        self.assertNotIn("genre", d)
        self.assertNotIn("characters", d)                          # no bare token left to mistake for one

    def test_genre_without_characters(self):
        d = self.m._parse_meta_line("Rated: T - English - Sci-Fi - Words: 1,000")
        self.assertEqual(d["genre"], "Sci-Fi")
        self.assertNotIn("characters", d)

    def test_community_crossover_prefix_stripped(self):
        d = self.m._parse_meta_line("crossover - Rated: T - English - Adventure - Words: 40,000 - id: 123")
        self.assertEqual(d["rating"], "T")
        self.assertEqual(d["genre"], "Adventure")


class StoryTest(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_story_metadata(self):
        with with_page(self.m, STORY):
            r = self.m.fic_meta("https://www.fanfiction.net/s/11111/1/")
        self.assertTrue(r.ok)
        d = r.data
        self.assertEqual(d["title"], "The Long Road")
        self.assertEqual(d["author"], "Writer")
        self.assertEqual(d["fandoms"], ["Harry Potter"])
        self.assertFalse(d["crossover"])
        self.assertIn("second chance", d["summary"])
        self.assertEqual(d["rating"], "T")
        self.assertEqual(d["genre"], "Adventure/Drama")
        self.assertEqual(d["characters"], "Harry P., Hermione G.")
        self.assertEqual(d["words"], "200,000")
        self.assertEqual(d["status"], "Complete")
        self.assertIn("## Metadata", r.text)
        self.assertIn("Fandom: Harry Potter", r.text)

    def test_crossover_flag_and_card(self):
        with with_page(self.m, CROSSOVER):
            r = self.m.fic_meta("https://www.fanfiction.net/s/222/1/")
        self.assertTrue(r.ok)
        self.assertTrue(r.data["crossover"])
        self.assertIn("Crossover:", r.text)
        self.assertEqual(r.data["rating"], "M")

    def test_bot_check_is_reported(self):
        with with_page(self.m, BOT_CHECK):
            r = self.m.fic_meta("https://www.fanfiction.net/s/11111/1/")
        self.assertFalse(r.ok)
        self.assertIn("bot-check", r.text)

    def test_writes_json(self):
        with tempfile.TemporaryDirectory() as t:
            out = os.path.join(t, "fic.json")
            with with_page(self.m, STORY):
                r = self.m.fic_meta("https://www.fanfiction.net/s/11111/1/", out=out)
            self.assertTrue(r.ok)
            with open(out, encoding="utf-8") as f:
                self.assertEqual(json.load(f)["characters"], "Harry P., Hermione G.")


class CommunityTest(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def test_lists_stories_with_metadata(self):
        with with_page(self.m, COMMUNITY):
            r = self.m.fic_community("https://www.fanfiction.net/community/Crossovers/1076/")
        self.assertTrue(r.ok)
        d = r.data
        self.assertEqual(d["community"], "Best Crossovers")
        self.assertEqual(d["count"], 2)
        first = d["stories"][0]
        self.assertEqual(first["title"], "Worlds Collide")
        self.assertEqual(first["author"], "Author")
        self.assertEqual(first["words"], "40,000")
        self.assertEqual(first["rating"], "T")
        self.assertIn("save two galaxies", first["summary"])
        self.assertNotIn("Rated:", first["summary"])               # meta stripped out of the summary blob
        self.assertEqual(d["stories"][1]["genre"], "Sci-Fi/Drama")
        self.assertIn("Worlds Collide", r.text)

    def test_query_filters_the_community(self):
        with with_page(self.m, COMMUNITY):
            r = self.m.fic_community("https://www.fanfiction.net/community/Crossovers/1076/", query="stargate")
        self.assertTrue(r.ok)
        self.assertEqual(r.data["count"], 1)                    # only "Second Dawn" mentions Stargate
        self.assertEqual(r.data["stories"][0]["title"], "Second Dawn")

    def test_query_with_no_match_is_reported(self):
        with with_page(self.m, COMMUNITY):
            r = self.m.fic_community("https://www.fanfiction.net/community/Crossovers/1076/", query="pokemon")
        self.assertFalse(r.ok)
        self.assertIn("match", r.text)

    def test_limit(self):
        with with_page(self.m, COMMUNITY):
            r = self.m.fic_community("https://www.fanfiction.net/community/Crossovers/1076/", limit=1)
        self.assertEqual(r.data["count"], 1)

    def test_empty_page_reported(self):
        with with_page(self.m, "<html><body><p>nothing</p></body></html>"):
            r = self.m.fic_community("https://www.fanfiction.net/community/Crossovers/1076/")
        self.assertFalse(r.ok)
        self.assertIn("No stories found", r.text)


STORY_CODEX = """<!doctype html><html><head><title>Ragnarok Rising, a Stargate + BSG crossover</title></head>
<body>
<div id=pre_story_links><span>
  <a class=xcontrast_txt href=/Stargate-and-Battlestar-Galactica-Crossovers/>Stargate + Battlestar Galactica Crossover</a></span></div>
<div id=storytext class='storytext xcontrast_txt nocopy'>
<p>The Ragnarok hung in high orbit, the largest warship humanity had ever built.</p>
<p>BS Ragnarok — Technical Readout</p>
<p>Class: Battlestar/Daedalus hybrid</p>
<p>Length: 1,900 meters</p>
<p>Primary Armament: 32 Asgard plasma beam cannons</p>
<p>Point Defense: Naquadah-enhanced railguns</p>
<p>FTL: Modified Mark III jump drive</p>
<p>Fighter Complement: 40 Vipers</p>
<p>Crew: 2,500</p>
<p>Shields: Asgard shield grid</p>
<p>Captain Shepard looked out at the stars and wondered what would come next for them all.</p>
</div></body></html>"""

SG_WIKI = {"stargate.fandom.com": {
    "search": {"Asgard": ["Asgard"], "Daedalus": ["Daedalus (BC-304)"], "Naquadah": ["Naquadah"],
               "Battlestar": [], "Vipers": []},
    "pages": {"Asgard": "The '''Asgard''' are an advanced benevolent race allied with Earth.",
              "Daedalus (BC-304)": "The '''Daedalus''' is a BC-304 battlecruiser of the Tau'ri.",
              "Naquadah": "'''Naquadah''' is a dense mineral central to Goa'uld technology."}}}


class RouterFake:
    """Returns the fic HTML for fanfiction.net and MediaWiki JSON for a wiki host."""

    def __init__(self, fic_html, wikis):
        self.fic_html, self.wikis, self.calls = fic_html, wikis, []

    def get(self, url):
        self.calls.append(url)
        parts = urlsplit(url)
        if parts.netloc.endswith("fanfiction.net"):
            return Resp(self.fic_html)
        q = {k: v[0] for k, v in parse_qs(parts.query).items()}
        wiki = self.wikis.get(parts.netloc)
        if wiki is None:
            return Resp("nope", status=404)
        if q.get("list") == "search":
            titles = wiki["search"].get(q.get("srsearch", ""), [])
            return Resp(json.dumps({"query": {"search": [{"title": t} for t in titles]}}),
                        content_type="application/json")
        if q.get("action") == "parse":
            wt = wiki["pages"].get(q.get("page", ""))
            if wt is None:
                return Resp(json.dumps({"error": {"code": "missingtitle"}}), content_type="application/json")
            return Resp(json.dumps({"parse": {"title": q["page"], "displaytitle": q["page"], "wikitext": wt,
                        "categories": []}}), content_type="application/json")
        return Resp("{}", content_type="application/json")


class SpecSheetTest(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def patch(self, fake):
        return mock.patch.object(self.m, "fetcher", lambda **kw: fake)

    def test_fic_text_returns_the_story_prose(self):
        with self.patch(RouterFake(STORY_CODEX, {})):
            r = self.m.fic_text("https://www.fanfiction.net/s/11111/7/")
        self.assertTrue(r.ok)
        self.assertIn("Ragnarok hung in high orbit", r.text)
        self.assertIn("Technical Readout", r.text)

    def test_spec_sheet_from_codex(self):
        with self.patch(RouterFake(STORY_CODEX, {})):
            r = self.m.fic_spec("https://www.fanfiction.net/s/11111/7/", "Ragnarok")
        self.assertTrue(r.ok)
        d = r.data
        self.assertEqual(d["specs"]["Length"], "1,900 meters")
        self.assertEqual(d["specs"]["Crew"], "2,500")
        self.assertEqual(d["specs"]["Primary Armament"], "32 Asgard plasma beam cannons")
        self.assertIn("Fighter Complement", d["specs"])
        self.assertNotIn("BS Ragnarok", d["specs"])             # the header line isn't a spec row
        self.assertIn("Technical Readout", d["heading"])
        self.assertNotIn("canon", d)                            # no wikis given
        for want in ("Asgard", "Naquadah", "Vipers"):
            self.assertIn(want, d["mentions"])
        self.assertNotIn("III", d["mentions"])                 # roman-numeral noise filtered
        self.assertIn("## Specifications", r.text)

    def test_spec_sheet_cross_references_canon_wikis(self):
        fake = RouterFake(STORY_CODEX, SG_WIKI)
        with self.patch(fake):
            r = self.m.fic_spec("https://www.fanfiction.net/s/11111/7/", "Ragnarok",
                                wikis="https://stargate.fandom.com")
        self.assertTrue(r.ok)
        canon = r.data["canon"]
        self.assertIn("Asgard", canon)
        self.assertEqual(canon["Asgard"]["title"], "Asgard")
        self.assertIn("advanced benevolent race", canon["Asgard"]["summary"])
        self.assertEqual(canon["Daedalus"]["title"], "Daedalus (BC-304)")
        self.assertIn("## Canon cross-reference", r.text)

    def test_no_codex_block_is_reported(self):
        plain = ("<html><body><div id=storytext><p>Just a normal story paragraph with no specs at all, "
                 "going on for a while about the characters.</p></div></body></html>")
        with self.patch(RouterFake(plain, {})):
            r = self.m.fic_spec("https://www.fanfiction.net/s/11111/7/", "Ragnarok")
        self.assertFalse(r.ok)
        self.assertIn("codex", r.text)

    def test_bot_check_reported(self):
        with self.patch(RouterFake(BOT_CHECK, {})):
            r = self.m.fic_spec("https://www.fanfiction.net/s/11111/7/", "Ragnarok")
        self.assertFalse(r.ok)
        self.assertIn("bot-check", r.text)


CAMPAIGN = """<!doctype html><html><head><title>SG1 in Faerun, ch 4</title></head><body>
<div id=storytext class='storytext xcontrast_txt'>
<p>Colonel O'Neill ducked behind the ruined pillar as the goblins charged.</p>
<p>[Skill – Fireball obtained]</p>
<p>[Level Up! O'Neill is now Level 5]</p>
<p>O'Neill had learned to channel naquadah through the Weave, a trick no mage of Neverwinter had seen.</p>
<p>O'Neill gained the ability to sense zat energy at a distance.</p>
<p>Daniel translated the runes while the fight raged, but nothing new came to him here.</p>
<p>[Class: Spellsword]</p>
</div></body></html>"""


class ProgressionTest(unittest.TestCase):
    def setUp(self):
        self.m = load()

    def patch(self, html):
        fake = mock.Mock(get=mock.Mock(return_value=Resp(html)))
        return mock.patch.object(self.m, "fetcher", lambda **kw: fake)

    def test_tracks_system_notifications_and_prose(self):
        with self.patch(CAMPAIGN):
            r = self.m.fic_progression("https://www.fanfiction.net/s/555/4/", "O'Neill")
        self.assertTrue(r.ok)
        texts = [e["text"] for e in r.data["timeline"]]
        self.assertIn("[Skill – Fireball obtained]", texts)
        self.assertIn("[Level Up! O'Neill is now Level 5]", texts)
        self.assertIn("[Class: Spellsword]", texts)
        self.assertTrue(any("channel naquadah through the Weave" in t for t in texts))
        self.assertTrue(any("sense zat energy" in t for t in texts))
        # Daniel's line mentions no progression for O'Neill and is left out
        self.assertFalse(any("Daniel translated" in t for t in texts))
        kinds = {e["kind"] for e in r.data["timeline"]}
        self.assertEqual(kinds, {"system", "prose"})

    def test_no_progression_is_reported(self):
        plain = "<html><body><div id=storytext><p>They walked quietly through the long, empty hall.</p></div></body></html>"
        with self.patch(plain):
            r = self.m.fic_progression("https://www.fanfiction.net/s/555/4/", "O'Neill")
        self.assertFalse(r.ok)
        self.assertIn("No progression", r.text)

    def test_bot_check_reported(self):
        with self.patch(BOT_CHECK):
            r = self.m.fic_progression("https://www.fanfiction.net/s/555/1/", "O'Neill")
        self.assertFalse(r.ok)
        self.assertIn("bot-check", r.text)

    def test_chapter_range_builds_urls(self):
        urls = self.m._chapter_urls("https://www.fanfiction.net/s/555/1/", "1-3")
        self.assertEqual(urls, ["https://www.fanfiction.net/s/555/1/",
                                "https://www.fanfiction.net/s/555/2/",
                                "https://www.fanfiction.net/s/555/3/"])


class RegistrationTest(unittest.TestCase):
    def test_registration_and_outside_content(self):
        from libra.skills import SkillRegistry

        reg = SkillRegistry()
        got = reg.load_plugins([os.path.join(ROOT, "plugins")], log=None)
        want = {"fic_meta", "fic_community", "fic_text", "fic_spec", "fic_progression"}
        self.assertEqual(want & set(got), want)
        for name in ("fic_meta", "fic_community", "fic_text", "fic_spec", "fic_progression"):
            sk = reg.get(name)
            self.assertTrue(sk.untrusted_output)                    # web content marked outside
            self.assertEqual((sk.risk, sk.claimed_risk), ("write", "read"))  # held until accepted


if __name__ == "__main__":
    unittest.main()
