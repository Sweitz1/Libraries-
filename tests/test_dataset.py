import json
import os
import tempfile
import unittest

from libra.dataset import Dataset, html_to_text, record_to_item, strip_gutenberg, suggest
from libra.train import load_corpus

BOOK = ("Header junk\n*** START OF THE PROJECT GUTENBERG EBOOK TEST ***\n"
        "It was a bright cold day in April, and the clocks were quietly ticking away.\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK TEST ***\nLicence text")


class DatasetTest(unittest.TestCase):
    def test_gutenberg_and_html(self):
        body = strip_gutenberg(BOOK)
        self.assertIn("bright cold day", body)
        self.assertNotIn("Licence", body)
        self.assertNotIn("Header", body)
        text = html_to_text("<html><head><title>x</title><script>bad()</script></head>"
                            "<body><nav>menu</nav><p>Hello &amp; welcome.</p><p>Second</p></body></html>")
        self.assertEqual(text, "Hello & welcome.\n\nSecond")

    def test_record_formats(self):
        self.assertEqual(record_to_item({"question": "Q?", "answer": "A."})[1][1]["content"], "A.")
        sharegpt = {"conversations": [{"from": "human", "value": "hi"}, {"from": "gpt", "value": "hello"}]}
        self.assertEqual([m["role"] for m in record_to_item(sharegpt)[1]], ["user", "assistant"])
        alpaca = {"instruction": "Add", "input": "2+2", "output": "4"}
        self.assertEqual(record_to_item(alpaca)[1][0]["content"], "Add\n\n2+2")
        export = {"current_node": "b", "mapping": {
            "a": {"parent": None, "message": {"author": {"role": "user"}, "content": {"parts": ["hey"]}}},
            "b": {"parent": "a", "message": {"author": {"role": "assistant"}, "content": {"parts": ["yo"]}}}}}
        self.assertEqual([m["content"] for m in record_to_item(export)[1]], ["hey", "yo"])

    def test_build_and_train_input(self):
        with tempfile.TemporaryDirectory() as d:
            src = os.path.join(d, "src")
            os.makedirs(src)
            with open(os.path.join(src, "book.txt"), "w") as f:
                f.write(BOOK)
            with open(os.path.join(src, "copy.txt"), "w") as f:
                f.write(BOOK)  # duplicate paragraphs are removed
            with open(os.path.join(src, "faq.csv"), "w") as f:
                f.write("question,answer\nWhat is Libra?,A model written from scratch.\n")
            with open(os.path.join(src, "chats.jsonl"), "w") as f:
                f.write(json.dumps({"messages": [{"role": "user", "content": "hi"},
                                                 {"role": "assistant", "content": "hello"}]}) + "\n")
            ds = Dataset()
            ds.add_path(src, log=lambda *_: None)
            out = os.path.join(d, "out")
            stats = ds.save(out)
            self.assertEqual(stats["documents"], 1)
            self.assertEqual(stats["conversations"], 2)
            self.assertGreaterEqual(stats["duplicates_removed"], 1)
            corpus = load_corpus([out])
            self.assertIn("<|assistant|>hello<|end|>", corpus)
            self.assertIn("bright cold day", corpus)
            self.assertIn(out, stats["suggestion"]["command"])

    def test_suggest(self):
        self.assertEqual(suggest(1000)["preset"], "tiny")
        self.assertEqual(suggest(10_000_000)["preset"], "medium")


if __name__ == "__main__":
    unittest.main()
