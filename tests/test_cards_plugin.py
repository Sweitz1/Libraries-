"""The `card` plugin: render a card from a saved record or a wiki URL, no network."""
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock
from urllib.parse import parse_qs, urlsplit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load():
    spec = importlib.util.spec_from_file_location("cards_plugin", os.path.join(ROOT, "plugins", "cards.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Resp:
    def __init__(self, text, status=200, content_type="application/json"):
        self.text, self.status, self.content_type = text, status, content_type


class WikiFake:
    def get(self, url):
        q = {k: v[0] for k, v in parse_qs(urlsplit(url).query).items()}
        if q.get("action") == "parse":
            wt = "{{Infobox starship\n| class=[[BC-304]]\n| crew=200\n}}\nThe '''Daedalus''' is a battlecruiser."
            return Resp(json.dumps({"parse": {"title": "Daedalus", "displaytitle": "Daedalus", "wikitext": wt,
                        "categories": [{"category": "BC-304"}]}}))
        return Resp(json.dumps({"query": {"search": []}}))


class CardPluginTest(unittest.TestCase):
    def setUp(self):
        self.m = load()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_card_from_json_record(self):
        rec = {"subject": "Ragnarok", "heading": "Technical Readout", "url": "https://ff/s/1/7/",
               "specs": {"Length": "1,900 m", "Crew": "2,500"}, "mentions": ["Asgard"]}
        src = os.path.join(self.tmp.name, "ragnarok.json")
        with open(src, "w") as f:
            json.dump(rec, f)
        out = os.path.join(self.tmp.name, "ragnarok.svg")
        r = self.m.card(src, out=out)
        self.assertTrue(r.ok)
        self.assertTrue(os.path.isfile(out))
        with open(out, encoding="utf-8") as f:
            svg = f.read()
        self.assertIn("Ragnarok", svg)
        self.assertIn("1,900 m", svg)
        self.assertIn("placeholder", r.text)                   # no --image given

    def test_card_from_wiki_url(self):
        out = os.path.join(self.tmp.name, "daedalus.svg")
        with mock.patch.object(self.m, "fetcher", lambda **kw: WikiFake()):
            r = self.m.card("https://stargate.fandom.com/wiki/Daedalus", out=out)
        self.assertTrue(r.ok)
        with open(out, encoding="utf-8") as f:
            svg = f.read()
        self.assertIn("Daedalus", svg)
        self.assertIn("BC-304", svg)

    def test_world_file_cards_first_entry(self):
        world = {"world": [{"title": "Asgard", "kind": "species", "fields": {"homeworld": "Othala"},
                            "summary": "", "categories": [], "url": "https://x/wiki/Asgard"}]}
        src = os.path.join(self.tmp.name, "world.json")
        with open(src, "w") as f:
            json.dump(world, f)
        r = self.m.card(src, out=os.path.join(self.tmp.name, "a.svg"))
        self.assertTrue(r.ok)
        self.assertEqual(r.data["title"], "Asgard")

    def test_png_without_converter_falls_back_to_svg(self):
        rec = {"title": "X", "fields": {"a": "b"}, "summary": "", "categories": [], "url": ""}
        src = os.path.join(self.tmp.name, "x.json")
        with open(src, "w") as f:
            json.dump(rec, f)
        out = os.path.join(self.tmp.name, "x.png")
        with mock.patch.object(self.m.shutil, "which", lambda t: None):
            r = self.m.card(src, out=out)
        self.assertTrue(r.ok)
        self.assertTrue(os.path.isfile(out[:-4] + ".svg"))     # SVG written even though PNG couldn't convert
        self.assertFalse(os.path.isfile(out))

    def test_registration(self):
        from libra.skills import SkillRegistry

        reg = SkillRegistry()
        got = reg.load_plugins([os.path.join(ROOT, "plugins")], log=None)
        self.assertIn("card", got)


if __name__ == "__main__":
    unittest.main()
