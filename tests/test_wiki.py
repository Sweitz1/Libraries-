"""libra.wiki: the shared MediaWiki/wikitext reader behind the lorebook and fanfiction plugins.
No network — a tiny fake supplies the API responses."""
import json
import unittest

from libra import wiki


class Resp:
    def __init__(self, text, status=200, content_type="application/json"):
        self.text, self.status, self.content_type = text, status, content_type


class WikitextTest(unittest.TestCase):
    def test_strip_wiki(self):
        self.assertEqual(wiki.strip_wiki("[[A|B]] and [[C]]"), "B and C")
        self.assertEqual(wiki.strip_wiki("'''bold''' {{tmpl|x}}<ref>n</ref> end"), "bold  end".replace("  ", " "))

    def test_split_params_respects_nesting(self):
        self.assertEqual(len(wiki.split_params("Infobox| a=[[x|y]] | b={{t|1|2}} | c=z")), 4)

    def test_infobox_fields(self):
        name, fields = wiki.infobox_fields("{{Infobox ship\n| class=[[BC-304]]\n| image=x.png\n| crew=200\n}}\nLead.")
        self.assertEqual(name, "Infobox ship")
        self.assertEqual(fields["class"], "BC-304")
        self.assertEqual(fields["crew"], "200")
        self.assertNotIn("image", fields)                       # file/layout field dropped

    def test_lead_summary_stops_at_heading(self):
        wt = "{{Infobox x|a=b}}\nThe '''Thing''' is real.\n== History ==\nlong tail"
        self.assertEqual(wiki.lead_summary(wt), "The Thing is real.")


class ApiTest(unittest.TestCase):
    def test_api_candidates(self):
        got = list(wiki.api_candidates("https://stargate.fandom.com/wiki/Thor"))
        self.assertIn("https://stargate.fandom.com/api.php", got)

    def test_article_url(self):
        self.assertEqual(wiki.article_url("https://x.fandom.com/api.php", "USS Daedalus"),
                         "https://x.fandom.com/wiki/USS_Daedalus")

    def test_api_search_and_parse(self):
        def fetch(url):
            if "list=search" in url:
                return Resp(json.dumps({"query": {"search": [{"title": "Thor"}]}}))
            return Resp(json.dumps({"parse": {"title": "Thor", "wikitext": "{{Infobox character|race=Asgard}}\nThor."}}))

        self.assertEqual(wiki.api_search("https://x/api.php", "Thor", 5, fetch), ["Thor"])
        parse = wiki.api_parse("https://x/api.php", "Thor", fetch)
        self.assertEqual(wiki.infobox_fields(parse["wikitext"])[1]["race"], "Asgard")

    def test_non_mediawiki_returns_none(self):
        self.assertIsNone(wiki.api_search("https://x/api.php", "q", 1, lambda u: Resp("<html>", content_type="text/html")))
        self.assertIsNone(wiki.api_parse("https://x/api.php", "q", lambda u: Resp("<html>", content_type="text/html")))


if __name__ == "__main__":
    unittest.main()
