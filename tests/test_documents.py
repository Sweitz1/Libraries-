"""Stdlib document extractors: DOCX, PPTX, XLSX, EPUB, ODT/ODP/ODS and RTF. Each sample is built in the
test with zipfile, so no binary fixtures are needed. Also: they flow into the knowledge base and datasets,
and malicious XML / zip bombs are refused."""
import io
import os
import tempfile
import unittest
import zipfile

from libra import documents


def zdoc(files: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, body in files.items():
            z.writestr(name, body)
    return buf.getvalue()


def docx(paragraphs):
    body = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragraphs)
    xml = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{body}</w:body></w:document>")
    return zdoc({"[Content_Types].xml": "<Types/>", "word/document.xml": xml})


def pptx(slides):
    files = {"[Content_Types].xml": "<Types/>"}
    for i, texts in enumerate(slides, 1):
        runs = "".join(f"<a:p><a:r><a:t>{t}</a:t></a:r></a:p>" for t in texts)
        files[f"ppt/slides/slide{i}.xml"] = (
            '<?xml version="1.0"?><p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"'
            ' xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
            f"<p:cSld><p:spTree>{runs}</p:spTree></p:cSld></p:sld>")
    return zdoc(files)


def xlsx(rows):
    strings = []
    for row in rows:
        for cell in row:
            if isinstance(cell, str) and cell not in strings:
                strings.append(cell)
    si = "".join(f"<si><t>{s}</t></si>" for s in strings)
    shared = f'<?xml version="1.0"?><sst xmlns="x">{si}</sst>'
    xml_rows = []
    for row in rows:
        cells = []
        for cell in row:
            if isinstance(cell, str):
                cells.append(f'<c t="s"><v>{strings.index(cell)}</v></c>')
            else:
                cells.append(f"<c><v>{cell}</v></c>")
        xml_rows.append(f"<row>{''.join(cells)}</row>")
    sheet = f'<?xml version="1.0"?><worksheet xmlns="x"><sheetData>{"".join(xml_rows)}</sheetData></worksheet>'
    return zdoc({"[Content_Types].xml": "<Types/>", "xl/sharedStrings.xml": shared,
                 "xl/worksheets/sheet1.xml": sheet})


def epub(chapters):
    files = {"mimetype": "application/epub+zip",
             "META-INF/container.xml": '<?xml version="1.0"?><container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
             '<rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>'}
    items, spine = [], []
    for i, html in enumerate(chapters, 1):
        name = f"ch{i}.xhtml"
        files[f"OEBPS/{name}"] = f"<html><body><p>{html}</p></body></html>"
        items.append(f'<item id="c{i}" href="{name}"/>')
        spine.append(f'<itemref idref="c{i}"/>')
    files["OEBPS/content.opf"] = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf">'
                                  f'<manifest>{"".join(items)}</manifest><spine>{"".join(spine)}</spine></package>')
    return zdoc(files)


def odf(paragraphs, table=None):
    body = "".join(f"<text:p>{p}</text:p>" for p in paragraphs)
    if table:
        rows = "".join("<table:table-row>" + "".join(f"<table:table-cell><text:p>{c}</text:p></table:table-cell>"
                                                      for c in row) + "</table:table-row>" for row in table)
        body += f"<table:table>{rows}</table:table>"
    content = ('<?xml version="1.0"?><office:document-content '
               'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
               'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
               'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0">'
               f"<office:body><office:text>{body}</office:text></office:body></office:document-content>")
    return zdoc({"mimetype": "application/vnd.oasis.opendocument.text", "content.xml": content})


class ExtractTest(unittest.TestCase):
    def test_docx(self):
        self.assertEqual(documents.extract(data=docx(["First line", "Second line"]), ext=".docx"),
                         "First line\nSecond line")

    def test_pptx(self):
        text = documents.extract(data=pptx([["Title", "bullet one"], ["Slide two"]]), ext=".pptx")
        self.assertEqual(text, "Title\nbullet one\n\nSlide two")

    def test_xlsx_shared_strings_and_numbers(self):
        text = documents.extract(data=xlsx([["Name", "Age"], ["Ana", 30], ["Bo", 25]]), ext=".xlsx")
        self.assertEqual(text, "Name\tAge\nAna\t30\nBo\t25")

    def test_epub_follows_the_spine(self):
        text = documents.extract(data=epub(["Chapter one text", "Chapter two text"]), ext=".epub")
        self.assertEqual(text.split("\n\n"), ["Chapter one text", "Chapter two text"])

    def test_odt_paragraphs_and_table(self):
        text = documents.extract(data=odf(["Intro paragraph"], table=[["a", "b"], ["c", "d"]]), ext=".ods")
        self.assertIn("Intro paragraph", text)
        self.assertIn("a\tb", text)
        self.assertIn("c\td", text)

    def test_rtf(self):
        rtf = (rb"{\rtf1\ansi {\fonttbl\f0 Arial;}\f0 Hello \b world\b0 \par "
               rb"line two\par caf\'e9 \u233? end}")
        text = documents.extract(data=rtf, ext=".rtf")
        self.assertIn("Hello world", text)
        self.assertIn("line two", text)
        self.assertIn("café", text)  # \'e9 hex and \u233 both give é
        self.assertNotIn("Arial", text)  # the font table was dropped

    def test_unsupported_and_corrupt(self):
        with self.assertRaises(documents.DocumentError):
            documents.extract(data=b"x", ext=".pdf")
        with self.assertRaises(documents.DocumentError):
            documents.extract(data=b"not a zip", ext=".docx")

    def test_xml_entity_bomb_is_refused(self):
        bomb = ('<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "ha">]>'
                "<w:document xmlns:w='x'><w:body><w:p><w:r><w:t>&lol;</w:t></w:r></w:p></w:body></w:document>")
        with self.assertRaises(documents.DocumentError):
            documents.extract(data=zdoc({"word/document.xml": bomb}), ext=".docx")

    def test_zip_bomb_size_cap_is_enforced(self):
        huge = zdoc({"word/document.xml": "A" * (documents.MAX_UNZIPPED + 1)})
        with self.assertRaises(documents.DocumentError):
            documents.extract(data=huge, ext=".docx")


class WiringTest(unittest.TestCase):
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

    def write(self, name, data):
        p = os.path.join(self.tmp.name, name)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def test_knowledge_base_answers_from_a_docx(self):
        from libra.knowledge import SUPPORTED, Workspace

        self.assertTrue({".docx", ".xlsx", ".epub", ".rtf"} <= SUPPORTED)
        path = self.write("notes.docx", docx(["The spare key is under the blue flowerpot.", "Bins go out Tuesday."]))
        ws = Workspace(os.path.join(self.tmp.name, "ws"))
        report = ws.add([path], log=None)
        self.assertEqual(report["added"], [path])
        ans = ws.ask("where is the spare key", clarify=False)
        self.assertEqual(ans.status, "answered")
        self.assertIn("blue flowerpot", ans.text)
        self.assertIn("notes.docx", ans.text)  # cited by its real file name

    def test_dataset_add_file_reads_a_spreadsheet(self):
        from libra.dataset import Dataset

        rows = [["City", "Country", "Population"], ["Oslo", "Norway", "700000"], ["Bergen", "Norway", "280000"],
                ["Tromso", "Norway", "77000"], ["Stavanger", "Norway", "140000"]]
        path = self.write("data.xlsx", xlsx(rows))
        d = Dataset()
        d.add_file(path, log=lambda *a: None)
        self.assertIn("Norway", "\n".join(d.texts))


if __name__ == "__main__":
    unittest.main()
