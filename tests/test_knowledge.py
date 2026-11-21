import json
import os
import tempfile
import unittest

from libra.knowledge import CLARIFY_MARKER, Workspace, parse_transcript, style_from_rules, terms

NOTES = """# Project Falcon notes

The Falcon budget for 2027 is 40,000 dollars, approved by Dana in October.
Most of it goes to cloud hosting and two contractors.

Our brand voice is warm, plain and direct. We never use jargon.
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:04.000
Welcome to the weekly sync.

00:01:05.500 --> 00:01:09.000
The Falcon launch is planned for March 3rd next year.

00:01:09.000 --> 00:01:12.000
QA starts two weeks before launch.
"""

ROADMAP = "Roadmap\n\nFalcon launch is planned for the spring, after QA signs off on the launch build.\n"


class KnowledgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        d = self.tmp.name
        self.files = os.path.join(d, "files")
        os.makedirs(self.files)
        with open(os.path.join(self.files, "notes.md"), "w") as f:
            f.write(NOTES)
        with open(os.path.join(self.files, "sync.vtt"), "w") as f:
            f.write(VTT)
        open(os.path.join(self.files, "sync.mp4"), "wb").close()  # the recording the transcript belongs to
        open(os.path.join(self.files, "photo.jpg"), "wb").close()  # media with no description
        with open(os.path.join(self.files, "scan.pdf"), "wb") as f:
            f.write(b"%PDF-1.4")
        self.ws = Workspace(os.path.join(d, "ws"))
        self.report = self.ws.add([self.files], log=None)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ingest_report(self):
        self.assertEqual(len(self.report["added"]), 2)
        self.assertTrue(self.report["unsupported"][0].endswith("scan.pdf"))
        self.assertTrue(self.report["media_without_text"][0].endswith("photo.jpg"))
        again = self.ws.add([self.files], log=None)
        self.assertEqual(len(again["unchanged"]), 2)

    def test_answer_with_line_citation(self):
        a = self.ws.ask("What is the Falcon budget?")
        self.assertEqual(a.status, "answered")
        self.assertIn("40,000 dollars", a.text)
        self.assertRegex(a.citations[0]["source"], r"notes\.md, lines? 3")

    def test_transcript_timestamp_citation(self):
        a = self.ws.ask("When is the launch?")
        self.assertEqual(a.status, "answered")
        self.assertIn("March 3rd", a.text)
        self.assertRegex(a.citations[0]["source"], r"sync\.mp4 \(transcript: sync\.vtt\) @ 00:01:05")

    def test_not_found_does_not_guess(self):
        a = self.ws.ask("Who won the football match?")
        self.assertEqual(a.status, "not_found")
        self.assertIn("won't guess", a.text)
        self.assertEqual(a.citations, [])

    def test_clarify_then_follow_up(self):
        with open(os.path.join(self.files, "roadmap.txt"), "w") as f:
            f.write(ROADMAP)
        self.ws.add([self.files], log=None)
        q = "When is the Falcon launch planned?"
        a = self.ws.ask(q)
        self.assertEqual(a.status, "clarify")
        self.assertTrue(a.text.startswith(CLARIFY_MARKER))
        self.assertEqual(len(a.questions), 1)
        convo = [{"role": "user", "content": q}, {"role": "assistant", "content": a.text}]
        pick = self.ws.ask_chat(convo + [{"role": "user", "content": "roadmap"}])
        self.assertEqual(pick.status, "answered")
        self.assertTrue(all("roadmap.txt" in c["source"] for c in pick.citations))
        both = self.ws.ask_chat(convo + [{"role": "user", "content": "all"}])
        self.assertEqual(both.status, "answered")
        self.assertEqual(self.ws.ask(q, clarify=False).status, "answered")

    def test_corrections_are_permanent(self):
        self.ws.correct("What is the Falcon budget?", "The budget is 45,000 dollars after the November revision.")
        reloaded = Workspace(self.ws.path)  # survives restarts
        a = reloaded.ask("what's the falcon budget")
        self.assertIn("45,000", a.text)
        self.assertIn("your correction", a.citations[0]["source"])

    def test_rules_change_style(self):
        self.ws.add_rule("Always answer in bullet points and keep it short.")
        a = Workspace(self.ws.path).ask("What is the Falcon budget and where does it go?")
        body = a.text.split("\n\nSources:")[0]
        self.assertTrue(body.startswith("- "))
        self.assertLessEqual(len(body.splitlines()), 2)
        st = style_from_rules([{"text": "Don't ask me clarifying questions"}])
        self.assertFalse(st.clarify)

    def test_export_training(self):
        self.ws.correct("Tone?", "Warm, plain and direct.")
        self.ws.add_rule("Be concise.")
        out = os.path.join(self.tmp.name, "train")
        stats = self.ws.export_training(out)
        self.assertEqual(stats["conversations"], 1)
        with open(os.path.join(out, "chat.jsonl")) as f:
            msgs = json.loads(f.readline())["messages"]
        self.assertEqual(msgs[0], {"role": "system", "content": "Be concise."})

    def test_precise_clean_answers(self):
        a = self.ws.ask("What does the budget pay for?")
        self.assertIn("cloud hosting", a.text)  # the "Most of it ..." follow-on sentence is included
        self.assertNotIn("#", a.text.split("Sources:")[0])  # headings are never quoted as answers
        self.assertNotIn("launch", a.text)  # unrelated sentences sharing only "Falcon" are left out
        self.assertEqual([c["source"] for c in a.citations], ["files/notes.md, line 3", "files/notes.md, line 4"])

    def test_server_grounded(self):
        import threading
        import urllib.request

        from libra.server import Engine, make_server

        server = make_server(Engine(None, None, workspace=self.ws), "127.0.0.1", 0)
        base = f"http://127.0.0.1:{server.server_address[1]}"
        threading.Thread(target=server.serve_forever, daemon=True).start()

        def post(path, body):
            req = urllib.request.Request(base + path, json.dumps(body).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as r:
                return json.load(r)

        try:
            res = post("/v1/chat/completions", {"messages": [{"role": "user", "content": "What is the Falcon budget?"}]})
            self.assertIn("40,000", res["choices"][0]["message"]["content"])
            self.assertEqual(res["libra_grounding"]["status"], "answered")
            post("/v1/workspace/correct", {"question": "What is the Falcon budget?", "answer": "50,000 dollars."})
            self.assertIn("50,000", post("/v1/workspace/ask", {"question": "what is the falcon budget"})["text"])
        finally:
            server.shutdown()
            server.server_close()

    def test_helpers(self):
        self.assertEqual(parse_transcript("1\n00:00:01,500 --> 00:00:02,000\n<i>Hi</i>\n"), [(1.5, 2.0, "Hi")])
        self.assertEqual(terms("The launches were planned"), ["launch", "plann"])


if __name__ == "__main__":
    unittest.main()
