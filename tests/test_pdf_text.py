"""The pdf_text plugin: a from-scratch, standard-library PDF text extractor (zlib for compressed streams),
with a pdftotext fallback. Tested on PDFs built in the test, so it needs no sample files and no poppler."""
import importlib.util
import os
import tempfile
import unittest
import zlib
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def load_plugin():
    spec = importlib.util.spec_from_file_location("pdf_text_plugin", os.path.join(ROOT, "plugins", "pdf_text.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def make_pdf(content: bytes, compress: bool = False) -> bytes:
    """A minimal one-page PDF whose content stream is ``content`` (a sequence of PDF text operators)."""
    stream = zlib.compress(content) if compress else content
    filt = b"/Filter /FlateDecode " if compress else b""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<<" + filt + b"/Length " + str(len(stream)).encode() + b">>\nstream\n" + stream + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    for i, body in enumerate(objs, 1):
        out += f"{i} 0 obj\n".encode() + body + b"\nendobj\n"
    return bytes(out)


class PdfTextTest(unittest.TestCase):
    def setUp(self):
        self.m = load_plugin()
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, data: bytes, name: str = "doc.pdf") -> str:
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_plain_and_compressed_and_multiline(self):
        content = (b"BT /F1 24 Tf 72 700 Td (Hello Libra) Tj "
                   b"0 -30 Td (Second line\\) with a paren) Tj "
                   b"0 -30 Td <4865780a> Tj ET")  # hex 'Hex\n'
        for compress in (False, True):
            r = self.m.pdf_text(self.write(make_pdf(content, compress)))
            self.assertTrue(r.ok, r.text)
            self.assertEqual(r.data["method"], "builtin")
            self.assertIn("Hello Libra", r.text)
            self.assertIn("Second line) with a paren", r.text)  # escaped paren decoded
            self.assertIn("Hex", r.text)
            lines = [l for l in r.data["text"].splitlines() if l.strip()]
            self.assertEqual(lines[:2], ["Hello Libra", "Second line) with a paren"])  # Td started new lines

    def test_writes_an_out_file_for_the_knowledge_base(self):
        out = os.path.join(self.tmp.name, "doc.txt")
        r = self.m.pdf_text(self.write(make_pdf(b"BT (Report body) Tj ET")), out=out)
        self.assertTrue(r.ok)
        with open(out, encoding="utf-8") as f:
            self.assertEqual(f.read().strip(), "Report body")
        self.assertIn("written to", r.text)

    def test_a_scanned_pdf_with_no_text_is_reported_clearly(self):
        r = self.m.pdf_text(self.write(make_pdf(b"q 1 0 0 1 0 0 cm /Im0 Do Q")))  # draws an image, no text
        self.assertFalse(r.ok)
        self.assertIn("OCR", r.text)

    def test_not_a_pdf_and_missing_file(self):
        with self.assertRaises(ValueError):
            self.m.pdf_text(self.write(b"just text, not a pdf", "x.pdf"))
        with self.assertRaises(ValueError):
            self.m.pdf_text(os.path.join(self.tmp.name, "nope.pdf"))

    def test_pdftotext_fallback_when_builtin_finds_little(self):
        # a PDF the builtin can't read (no decodable text operators), but pdftotext can
        pdf = self.write(make_pdf(b"q /Weird Do Q"))

        def fake_pdftotext(path, pages):
            self.assertTrue(os.path.isfile(path))
            return "text pulled by pdftotext\n"

        with mock.patch.object(self.m, "_pdftotext", fake_pdftotext):
            r = self.m.pdf_text(pdf, method="auto")
        self.assertTrue(r.ok)
        self.assertEqual(r.data["method"], "pdftotext")
        self.assertIn("pulled by pdftotext", r.text)

        # with no pdftotext available, the same PDF reports the scanned-PDF message instead of crashing
        with mock.patch.object(self.m, "_pdftotext", lambda p, pg: None), \
                mock.patch.object(self.m.shutil, "which", lambda n: None):
            self.assertFalse(self.m.pdf_text(pdf, method="auto").ok)

    def test_router_and_registration(self):
        from libra.skills import SkillRegistry

        reg = SkillRegistry()
        self.assertEqual(reg.load_plugins([os.path.join(ROOT, "plugins")], log=None).count("pdf_text"), 1)
        s = reg.get("pdf_text")
        # the plugin isn't accepted here, so its own "read" label is held as write until you accept the file
        self.assertEqual((s.risk, s.claimed_risk), ("write", "read"))
        self.assertEqual(self.m._route("extract text from /home/me/report.pdf please"), {"path": "/home/me/report.pdf"})
        self.assertIsNone(self.m._route("what is 2 + 2"))


if __name__ == "__main__":
    unittest.main()
