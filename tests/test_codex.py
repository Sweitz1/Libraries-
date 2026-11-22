"""The lore manager / world engine: libra.codex store and the codex_* plugin skills. Temp LIBRA_HOME."""
import importlib.util
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_plugin():
    spec = importlib.util.spec_from_file_location("codex_plugin", os.path.join(ROOT, "plugins", "codex.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class CodexBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = os.path.join(self.tmp.name, "home")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def make_png(self, name="art.png"):
        from libra.raster import Canvas

        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(Canvas(4, 4, (10, 20, 30)).to_png())
        return p


class StoreTest(CodexBase):
    def test_default_continuity_and_save(self):
        from libra import codex

        conts = codex.continuities()
        self.assertEqual(conts[0]["slug"], "main-canon")
        self.assertEqual(conts[0]["type"], "canon")
        e = codex.save("USS Daedalus", kind="ship", fields={"class": "BC-304"})
        self.assertEqual(e["continuity"], "main-canon")
        self.assertEqual(e["type"], "tech")                     # classified from kind 'ship'
        self.assertEqual(codex.get("USS Daedalus")["fields"]["class"], "BC-304")

    def test_same_subject_separate_per_continuity(self):
        from libra import codex

        codex.add_continuity("Frostline AU", "au")
        codex.save("Thor", type="character", fields={"rank": "Supreme Commander"})
        codex.save("Thor", type="character", fields={"rank": "Mortal"}, continuity="Frostline AU")
        self.assertEqual(codex.get("Thor")["fields"]["rank"], "Supreme Commander")          # canon
        self.assertEqual(codex.get("Thor", "Frostline AU")["fields"]["rank"], "Mortal")     # AU, kept apart
        self.assertEqual(len(codex.all_entries()), 2)
        self.assertEqual(len(codex.all_entries("main-canon")), 1)

    def test_typed_grouping_and_classify(self):
        from libra import codex

        codex.save("Erin", type="character")
        codex.save("Liscor", kind="city")
        codex.save("The Horns", kind="guild")
        groups = codex.by_type("main-canon")
        self.assertEqual(set(groups), {"character", "location", "faction"})
        self.assertEqual(codex.classify_type("starship"), "tech")

    def test_timeline_sorted_by_date(self):
        from libra import codex

        codex.save("Second event", type="event", date="2005")
        codex.save("First event", type="event", date="2004")
        codex.save("Undated", type="event")
        tl = codex.timeline("main-canon")
        self.assertEqual([e["title"] for e in tl], ["First event", "Second event"])          # dated only, in order

    def test_cross_world_links_auto_and_explicit(self):
        from libra import codex

        codex.add_continuity("Modern AU", "au")
        codex.save("Thor", type="character")
        codex.save("Thor", type="character", continuity="Modern AU")                          # same slug → auto link
        main = codex.get("Thor")
        linked = codex.links_of(main)
        self.assertEqual([c["slug"] for c, _ in linked], ["modern-au"])

    def test_conflicts(self):
        from libra import codex

        clashes = codex.conflicts({"fields": {"rank": "SC", "race": "Asgard"}},
                                  {"rank": "Admiral", "race": "Asgard", "age": "900"})
        self.assertEqual(clashes, {"rank": ("SC", "Admiral")})

    def test_image_copied_per_continuity(self):
        from libra import codex

        e = codex.save("Ship", image=self.make_png())
        self.assertEqual(e["image"], "images/main-canon/ship.png")
        self.assertTrue(os.path.isfile(codex.image_path(e)))

    def test_legacy_flat_entries_migrate_into_canon(self):
        from libra import codex

        # write a legacy flat entry file (pre-continuity layout)
        root = os.path.join(codex.base(), "entries")
        os.makedirs(root, exist_ok=True)
        with open(os.path.join(root, "thor.json"), "w") as f:
            json.dump({"slug": "thor", "title": "Thor", "kind": "character", "fields": {"race": "Asgard"},
                       "body": "", "categories": []}, f)
        e = codex.get("Thor")                                   # continuities()/migrate runs on access
        self.assertEqual(e["continuity"], "main-canon")
        self.assertEqual(e["type"], "character")
        self.assertFalse(os.path.isfile(os.path.join(root, "thor.json")))


class PluginTest(CodexBase):
    def setUp(self):
        super().setUp()
        self.m = load_plugin()

    def test_add_search_get(self):
        r = self.m.codex_add("USS Daedalus", type="tech", fields="class=BC-304; crew=200",
                             body="Earth's second BC-304.", categories="Ships")
        self.assertTrue(r.ok)
        self.assertIn("Main Canon", r.text)
        s = self.m.codex_search("BC-304")
        self.assertEqual(s.data["entries"][0]["title"], "USS Daedalus")
        self.assertIn("# USS Daedalus", self.m.codex_get("USS Daedalus").text)

    def test_continuity_and_au_separation(self):
        self.m.codex_continuity("Frostline AU", type="au")
        self.m.codex_add("Thor", type="character", fields="rank=Supreme Commander")
        self.m.codex_add("Thor", type="character", fields="rank=Mortal", continuity="Frostline AU")
        self.assertEqual(self.m.codex_get("Thor").data["fields"]["rank"], "Supreme Commander")
        self.assertEqual(self.m.codex_get("Thor", "Frostline AU").data["fields"]["rank"], "Mortal")
        tiers = self.m.codex_continuity()
        self.assertIn("Frostline AU", tiers.text)

    def test_contradiction_flag_then_force(self):
        self.m.codex_add("Thor", type="character", fields="rank=Supreme Commander")
        flagged = self.m.codex_add("Thor", type="character", fields="rank=Fleet Admiral")
        self.assertFalse(flagged.ok)
        self.assertIn("rank", flagged.data["conflicts"])
        from libra import codex

        self.assertEqual(codex.get("Thor")["fields"]["rank"], "Supreme Commander")
        forced = self.m.codex_add("Thor", type="character", fields="rank=Fleet Admiral", force=True)
        self.assertTrue(forced.ok)
        self.assertEqual(codex.get("Thor")["fields"]["rank"], "Fleet Admiral")

    def test_new_field_does_not_flag(self):
        self.m.codex_add("Ship", fields="class=BC-304")
        self.assertTrue(self.m.codex_add("Ship", fields="class=BC-304; crew=200").ok)

    def test_timeline_skill(self):
        self.m.codex_add("Battle", type="event", date="2004")
        r = self.m.codex_timeline()
        self.assertTrue(r.ok)
        self.assertIn("2004", r.text)

    def test_link_skill(self):
        self.m.codex_continuity("Modern AU", type="au")
        self.m.codex_add("Thor", type="character")
        self.m.codex_add("Thor", type="character", continuity="Modern AU")
        r = self.m.codex_link("Thor", to="modern-au/thor")
        self.assertTrue(r.ok)

    def test_card_png_and_svg(self):
        self.m.codex_add("Ragnarok", type="tech", fields="length=1,900 m", image=self.make_png())
        svg_out = os.path.join(self.tmp.name, "r.svg")
        self.assertTrue(self.m.codex_card("Ragnarok", out=svg_out).ok)
        with open(svg_out, encoding="utf-8") as f:
            self.assertIn("<image", f.read())
        png_out = os.path.join(self.tmp.name, "r.png")
        self.m.codex_card("Ragnarok", out=png_out)
        with open(png_out, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

    def test_import_into_continuity(self):
        rec = {"subject": "Ragnarok", "specs": {"Length": "1,900 m"}, "mentions": ["Asgard"],
               "url": "https://ff/s/1/7/"}
        src = os.path.join(self.tmp.name, "r.json")
        with open(src, "w") as f:
            json.dump(rec, f)
        r = self.m.codex_import(src, continuity="Sources")       # creates the AU 'Sources'
        self.assertTrue(r.ok)
        from libra import codex

        e = codex.get("Ragnarok", "Sources")
        self.assertEqual(e["fields"]["Length"], "1,900 m")
        self.assertEqual(e["source"], "https://ff/s/1/7/")

    def test_build_static_site(self):
        self.m.codex_continuity("Frostline AU", type="au")
        self.m.codex_add("USS Daedalus", type="tech", fields="class=BC-304",
                         body="A ship.\n== History ==\nBuilt by the Tau'ri.")
        self.m.codex_add("Thor", type="character", continuity="Frostline AU")
        out = os.path.join(self.tmp.name, "site")
        r = self.m.codex_build(out)
        self.assertTrue(r.ok)
        self.assertEqual(r.data["entries"], 2)
        self.assertEqual(r.data["continuities"], 2)
        self.assertTrue(os.path.isfile(os.path.join(out, "index.html")))
        self.assertTrue(os.path.isfile(os.path.join(out, "wiki", "main-canon", "uss-daedalus.html")))
        self.assertTrue(os.path.isfile(os.path.join(out, "wiki", "frostline-au", "thor.html")))
        with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
            idx = f.read()
        self.assertIn("Main Canon", idx)
        self.assertIn("Frostline AU", idx)
        self.assertIn('href="wiki/main-canon/uss-daedalus.html"', idx)
        with open(os.path.join(out, "wiki", "main-canon", "uss-daedalus.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn('class="portable-infobox"', page)
        self.assertIn("History", page)
        self.assertIn('href="../../index.html"', page)

    def test_export_markdown(self):
        self.m.codex_add("Thing", type="item", body="It does things.")
        out = os.path.join(self.tmp.name, "md")
        self.assertTrue(self.m.codex_export(out).ok)
        self.assertTrue(os.path.isfile(os.path.join(out, "thing.md")))

    def test_registration(self):
        from libra.skills import SkillRegistry

        reg = SkillRegistry()
        got = reg.load_plugins([os.path.join(ROOT, "plugins")], log=None)
        want = {"codex_add", "codex_search", "codex_get", "codex_card", "codex_import", "codex_export",
                "codex_build", "codex_serve", "codex_continuity", "codex_timeline", "codex_link"}
        self.assertEqual(want & set(got), want)
        self.assertEqual(reg.get("codex_add").risk, "write")


if __name__ == "__main__":
    unittest.main()
