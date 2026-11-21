"""`libra eval kb`: grounded answers from your files, measured three ways (right, wrong, honest refusals).
Also a benchmark on Libra's own docs, with the questions worded differently from the text. Its floor
guards against a change that makes answers worse, and every unanswerable question must be refused."""
import json
import os
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ["README.md", "LEARN.md", "INSTALL.md", "SECURITY.md", "CONTRIBUTING.md", "deploy/README.md",
        "docs/MOBILE.md", "docs/REVIEW.md", "clients/java/README.md"]


class KBEvalTest(unittest.TestCase):
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

    def test_scoring(self):
        from libra import evaluate
        from libra.knowledge import Workspace

        notes = os.path.join(self.tmp.name, "notes.md")
        with open(notes, "w", encoding="utf-8") as f:
            f.write("The boiler is serviced every May by the plumber.\n\n"
                    "The car insurance renews in October each year.\n")
        ws = Workspace(os.path.join(self.tmp.name, "ws"))
        ws.add([notes], log=None)
        suite = os.path.join(self.tmp.name, "s.jsonl")
        with open(suite, "w", encoding="utf-8") as f:
            for item in ({"question": "when is the boiler serviced", "expect": "May"},
                         {"question": "when does the car insurance renew", "expect": "May"},  # answered, wrong place
                         {"question": "what colour is the front door", "expect": "blue"},       # not in the files
                         {"question": "who won the football world cup", "unanswerable": True}):
                f.write(json.dumps(item) + "\n")
        r = evaluate.kb_suite(ws, suite)
        self.assertEqual((r["answerable"], r["right"], r["wrong"], r["refused"]), (3, 0.3333, 0.3333, 1.0))
        self.assertEqual(len(r["failures"]), 2)
        self.assertIn("knowledge base on s.jsonl", evaluate.describe(r))
        self.assertIsNone(evaluate.record(r))
        self.assertEqual(evaluate.headline(evaluate.record(r)), {"right": 0.3333, "wrong": 0.3333, "refused": 1.0})

    def test_libras_own_docs(self):
        from libra import evaluate
        from libra.knowledge import Workspace

        ws = Workspace(os.path.join(self.tmp.name, "ws"))
        ws.add([os.path.join(ROOT, d) for d in DOCS], log=None)
        r = evaluate.kb_suite(ws, evaluate.LIBRA_DOCS_KB_SUITE)
        self.assertEqual(r["answerable"], 24)
        self.assertEqual(r["refused"], 1.0, "a question the docs can't answer got an answer")
        # 9 of 24 when this benchmark was written; the docs change, so the floor leaves a little room
        self.assertGreaterEqual(r["right"], 7 / 24, evaluate.describe(r))
        self.assertLessEqual(r["wrong"], 7 / 24, evaluate.describe(r))


if __name__ == "__main__":
    unittest.main()
