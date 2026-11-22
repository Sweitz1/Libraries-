"""libra.card: render a Fandom-style card as SVG, and normalise records into a card model. No network."""
import unittest

from libra import card


class ModelTest(unittest.TestCase):
    def test_worldbook_entry_model(self):
        rec = {"title": "Thor", "kind": "character", "summary": "An Asgard commander.",
               "fields": {"race": "Asgard", "rank": "Supreme Commander"},
               "categories": ["Asgard", "Characters"], "url": "https://x/wiki/Thor"}
        m = card.card_model(rec)
        self.assertEqual(m["title"], "Thor")
        self.assertEqual(m["subtitle"], "character")
        self.assertEqual(m["sections"][0]["rows"][0], ("race", "Asgard"))
        self.assertTrue(any("Categories:" in f for f in m["footer"]))
        self.assertIsNone(m["image"])

    def test_fic_spec_model(self):
        rec = {"subject": "Ragnarok", "heading": "Technical Readout", "url": "https://ff/s/1/7/",
               "specs": {"Length": "1,900 m", "Crew": "2,500"}, "mentions": ["Asgard", "Vipers"],
               "canon": {"Asgard": {"title": "Asgard", "summary": "An advanced race."}}}
        m = card.card_model(rec)
        self.assertEqual(m["title"], "Ragnarok")
        heads = [s["heading"] for s in m["sections"]]
        self.assertEqual(heads, ["Specifications", "References", "Canon cross-reference"])

    def test_progression_model(self):
        rec = {"character": "O'Neill", "url": "https://ff/s/1/1/",
               "timeline": [{"at": "ch 3", "kind": "system", "text": "[Skill – Fireball]"}]}
        m = card.card_model(rec)
        self.assertEqual(m["title"], "O'Neill")
        self.assertEqual(m["sections"][0]["heading"], "Evolution")
        self.assertEqual(m["sections"][0]["rows"][0], ("ch 3", "[Skill – Fireball]"))


class RenderTest(unittest.TestCase):
    def test_svg_is_well_formed_and_contains_data(self):
        import xml.etree.ElementTree as ET

        m = {"title": "USS Daedalus", "subtitle": "ship", "image": None,
             "sections": [{"heading": "Specifications", "rows": [("class", "BC-304"), ("crew", "200")]}],
             "footer": ["Source: https://x/wiki/Daedalus"]}
        svg = card.render_card(m)
        root = ET.fromstring(svg)                              # parses => well-formed XML
        self.assertEqual(root.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertIn("USS Daedalus", svg)
        self.assertIn("BC-304", svg)
        self.assertGreater(int(root.get("height")), 100)

    def test_xml_is_escaped(self):
        svg = card.render_card({"title": "A & B <ship>", "sections": [], "footer": []})
        self.assertIn("A &amp; B &lt;ship&gt;", svg)
        self.assertNotIn("<ship>", svg)

    def test_placeholder_when_no_image(self):
        svg = card.render_card({"title": "X", "sections": [], "footer": []})
        self.assertIn("no illustration", svg)
        self.assertIn("no image model", svg)

    def test_long_value_wraps(self):
        long = "a very long armament description " * 6
        svg = card.render_card({"title": "Ship", "image": None,
                                "sections": [{"heading": "", "rows": [("Armament", long)]}], "footer": []})
        # the value is split across several <text> runs rather than one overflowing line
        self.assertGreater(svg.count("<text"), 3)

    def test_wrap_hard_splits_overlong_word(self):
        lines = card._wrap("x" * 200, 12, card.VALUE_W)
        self.assertTrue(len(lines) > 1 and all(len(ln) <= 40 for ln in lines))


if __name__ == "__main__":
    unittest.main()
