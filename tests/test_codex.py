"""The personal fan wiki: libra.codex store and the codex_* plugin skills. Uses a temp LIBRA_HOME."""
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
    def test_save_get_and_slug(self):
        from libra import codex

        e = codex.save("USS Daedalus", kind="ship", fields={"class": "BC-304", "crew": "200"},
                       body="A battlecruiser.", categories=["Ships", "Tau'ri"])
        self.assertEqual(e["slug"], "uss-daedalus")
        self.assertEqual(codex.get("uss-daedalus")["fields"]["class"], "BC-304")
        self.assertEqual(codex.get("USS Daedalus")["title"], "USS Daedalus")   # by title too
        self.assertIsNone(codex.get("nope"))

    def test_update_replaces(self):
        from libra import codex

        codex.save("Thor", kind="character", fields={"race": "Asgard"})
        codex.save("Thor", kind="character", fields={"race": "Asgard", "rank": "Commander"})
        self.assertEqual(len(codex.all_entries()), 1)
        self.assertIn("rank", codex.get("Thor")["fields"])

    def test_image_is_copied_in(self):
        from libra import codex

        e = codex.save("Ship", image=self.make_png())
        self.assertEqual(e["image"], "images/ship.png")
        self.assertTrue(os.path.isfile(codex.image_path(e)))

    def test_bad_image_rejected(self):
        from libra import codex

        bad = os.path.join(self.tmp.name, "notes.txt")
        open(bad, "w").close()
        with self.assertRaises(ValueError):
            codex.save("X", image=bad)

    def test_search_ranks_by_terms(self):
        from libra import codex

        codex.save("USS Daedalus", kind="ship", fields={"class": "BC-304"}, categories=["Ships"])
        codex.save("Thor", kind="character", body="Asgard commander")
        hits = codex.search("BC-304 ship")
        self.assertEqual(hits[0]["title"], "USS Daedalus")
        self.assertEqual(codex.search("asgard")[0]["title"], "Thor")

    def test_canon_tier_stored_and_filtered(self):
        from libra import codex

        codex.save("Thor", kind="character", fields={"race": "Asgard"}, canon="official")
        codex.save("Erin", kind="character", canon="mine")
        self.assertEqual(codex.get("Thor")["canon"], "official")
        self.assertEqual([e["title"] for e in codex.search("", tier="mine")], ["Erin"])
        self.assertIn("official", codex.by_tier())
        with self.assertRaises(ValueError):
            codex.save("X", canon="nonsense")

    def test_conflicts_detects_changed_fields(self):
        from libra import codex

        existing = {"fields": {"rank": "Supreme Commander", "race": "Asgard"}}
        clashes = codex.conflicts(existing, {"rank": "Fleet Admiral", "race": "Asgard", "age": "900"})
        self.assertEqual(clashes, {"rank": ("Supreme Commander", "Fleet Admiral")})   # changed only; new field ok

    def test_markdown_and_record(self):
        from libra import codex

        codex.save("Item", kind="tech", fields={"power": "high"}, body="A gadget.", categories=["Tech"])
        md = codex.to_markdown(codex.get("Item"))
        self.assertIn("# Item", md)
        self.assertIn("**power:** high", md)
        self.assertEqual(codex.to_record(codex.get("Item"))["summary"], "A gadget.")


class PluginTest(CodexBase):
    def setUp(self):
        super().setUp()
        self.m = load_plugin()

    def test_add_search_get_flow(self):
        r = self.m.codex_add("USS Daedalus", kind="ship",
                             fields="class=BC-304; armament=Asgard beams; crew=200",
                             body="Earth's second BC-304.", categories="Ships, Tau'ri")
        self.assertTrue(r.ok)
        self.assertEqual(r.data["fields"]["armament"], "Asgard beams")
        s = self.m.codex_search("BC-304")
        self.assertTrue(s.ok)
        self.assertEqual(s.data["entries"][0]["title"], "USS Daedalus")
        g = self.m.codex_get("USS Daedalus")
        self.assertIn("# USS Daedalus", g.text)

    def test_add_with_image_then_card_svg_embeds_it(self):
        self.m.codex_add("Ragnarok", kind="ship", fields="length=1,900 m", image=self.make_png())
        out = os.path.join(self.tmp.name, "ragnarok.svg")
        r = self.m.codex_card("Ragnarok", out=out)
        self.assertTrue(r.ok)
        with open(out, encoding="utf-8") as f:
            svg = f.read()
        self.assertIn("<image", svg)                           # the stored image is embedded
        self.assertIn("1,900 m", svg)

    def test_card_png_renders(self):
        self.m.codex_add("Thor", kind="character", fields="race=Asgard")
        out = os.path.join(self.tmp.name, "thor.png")
        r = self.m.codex_card("Thor", out=out)
        self.assertTrue(r.ok)
        with open(out, "rb") as f:
            self.assertEqual(f.read(8), b"\x89PNG\r\n\x1a\n")

    def test_contradiction_is_flagged_then_forced(self):
        self.m.codex_add("Thor", kind="character", fields="rank=Supreme Commander", canon="official")
        flagged = self.m.codex_add("Thor", kind="character", fields="rank=Fleet Admiral", canon="official")
        self.assertFalse(flagged.ok)                           # established lore change, not saved
        self.assertIn("rank", flagged.data["conflicts"])
        from libra import codex

        self.assertEqual(codex.get("Thor")["fields"]["rank"], "Supreme Commander")   # unchanged
        forced = self.m.codex_add("Thor", kind="character", fields="rank=Fleet Admiral", canon="official",
                                  force=True)
        self.assertTrue(forced.ok)
        self.assertEqual(codex.get("Thor")["fields"]["rank"], "Fleet Admiral")

    def test_new_fields_do_not_trigger_a_flag(self):
        self.m.codex_add("Ship", fields="class=BC-304", canon="mine")
        r = self.m.codex_add("Ship", fields="class=BC-304; crew=200", canon="mine")   # adds crew, keeps class
        self.assertTrue(r.ok)

    def test_import_tags_canon_and_source(self):
        rec = {"title": "Asgard", "kind": "species", "fields": {"homeworld": "Othala"},
               "summary": "A race.", "categories": ["Races"], "url": "https://stargate.fandom.com/wiki/Asgard"}
        src = os.path.join(self.tmp.name, "a.json")
        with open(src, "w") as f:
            json.dump(rec, f)
        self.m.codex_import(src, canon="fanon")
        from libra import codex

        e = codex.get("Asgard")
        self.assertEqual(e["canon"], "fanon")
        self.assertEqual(e["source"], "https://stargate.fandom.com/wiki/Asgard")

    def test_build_static_site(self):
        self.m.codex_add("USS Daedalus", kind="ship", fields="class=BC-304",
                         body="A battlecruiser.\n== History ==\nBuilt by the Tau'ri.", canon="official")
        self.m.codex_add("Erin", kind="character", canon="mine", body="An innkeeper.")
        out = os.path.join(self.tmp.name, "site")
        r = self.m.codex_build(out)
        self.assertTrue(r.ok)
        self.assertEqual(r.data["entries"], 2)
        self.assertTrue(os.path.isfile(os.path.join(out, "index.html")))
        self.assertTrue(os.path.isfile(os.path.join(out, "wiki", "uss-daedalus.html")))
        with open(os.path.join(out, "index.html"), encoding="utf-8") as f:
            idx = f.read()
        self.assertIn("Official canon", idx)                   # grouped by tier
        self.assertIn("Your canon", idx)
        self.assertIn('href="wiki/uss-daedalus.html"', idx)    # static relative links
        with open(os.path.join(out, "wiki", "uss-daedalus.html"), encoding="utf-8") as f:
            page = f.read()
        self.assertIn('class="portable-infobox"', page)
        self.assertIn("History", page)
        self.assertIn('href="../index.html"', page)            # home link rewritten for subdir

    def test_import_fic_spec_record(self):
        rec = {"subject": "Ragnarok", "heading": "Technical Readout",
               "specs": {"Length": "1,900 m", "Crew": "2,500"}, "mentions": ["Asgard", "Vipers"]}
        src = os.path.join(self.tmp.name, "r.json")
        with open(src, "w") as f:
            json.dump(rec, f)
        r = self.m.codex_import(src)
        self.assertTrue(r.ok)
        from libra import codex

        e = codex.get("Ragnarok")
        self.assertEqual(e["fields"]["Length"], "1,900 m")
        self.assertEqual(e["kind"], "ship")

    def test_import_world_file_many_entries(self):
        world = {"world": [{"title": "Asgard", "kind": "species", "fields": {"homeworld": "Othala"},
                            "summary": "A race.", "categories": ["Races"]},
                           {"title": "Daedalus", "kind": "ship", "fields": {"class": "BC-304"},
                            "summary": "", "categories": []}]}
        src = os.path.join(self.tmp.name, "world.json")
        with open(src, "w") as f:
            json.dump(world, f)
        r = self.m.codex_import(src)
        self.assertEqual(r.data["count"], 2)

    def test_export_writes_markdown_for_kb(self):
        self.m.codex_add("Thing", kind="tech", body="It does things.")
        out = os.path.join(self.tmp.name, "md")
        r = self.m.codex_export(out)
        self.assertTrue(r.ok)
        self.assertTrue(os.path.isfile(os.path.join(out, "thing.md")))

    def test_registration(self):
        from libra.skills import SkillRegistry

        reg = SkillRegistry()
        got = reg.load_plugins([os.path.join(ROOT, "plugins")], log=None)
        want = {"codex_add", "codex_search", "codex_get", "codex_card", "codex_import", "codex_export",
                "codex_build", "codex_serve"}
        self.assertEqual(want & set(got), want)
        # codex_add changes local data: a write skill (and its label held until the plugin is accepted)
        self.assertEqual(reg.get("codex_add").risk, "write")


if __name__ == "__main__":
    unittest.main()
