"""Extract plain text from rich document formats, using only the standard library.

Office and ebook formats are ZIP archives of XML (DOCX, PPTX, XLSX, EPUB, ODT/ODP/ODS); RTF is text with
control words. None need a third-party library: `zipfile`, `xml.etree.ElementTree` and `re` are enough.
The text then flows into the knowledge base (`libra kb add`) and training data (`libra data`) like any file.

What this does NOT do: images, audio and video (no text to read — see the OCR/transcription plugins), and
PDF (its own `plugins/pdf_text.py`). Encrypted or password-protected files raise a clear error.

Safety: untrusted documents can carry XML-entity bombs (billion laughs, external entities) and zip bombs,
so a DOCTYPE/ENTITY is refused and the uncompressed size is capped.
"""
from __future__ import annotations

import io
import os
import re
import xml.etree.ElementTree as ET
import zipfile

DOC_EXT = {".docx", ".pptx", ".xlsx", ".epub", ".odt", ".odp", ".ods", ".rtf"}
MAX_UNZIPPED = 200_000_000  # refuse a zip that expands past this (zip bomb guard)
_DOCTYPE = re.compile(rb"<!(?:DOCTYPE|ENTITY)\b", re.I)


class DocumentError(ValueError):
    pass


def _xml_root(data: bytes) -> ET.Element:
    if _DOCTYPE.search(data[:4096]):  # block XXE and entity-expansion bombs before parsing
        raise DocumentError("refusing XML with a DOCTYPE/ENTITY declaration")
    try:
        return ET.fromstring(data)
    except ET.ParseError as exc:
        raise DocumentError(f"malformed XML: {exc}") from None


def _local(tag: str) -> str:
    """The tag name without its XML namespace: ``{...}p`` -> ``p``."""
    return tag.rsplit("}", 1)[-1]


def _open_zip(path: str | None, data: bytes | None) -> zipfile.ZipFile:
    try:
        z = zipfile.ZipFile(io.BytesIO(data) if data is not None else path)
    except zipfile.BadZipFile:
        raise DocumentError("not a valid ZIP-based document (corrupt or password-protected)") from None
    total = sum(i.file_size for i in z.infolist())
    if total > MAX_UNZIPPED:
        z.close()
        raise DocumentError(f"document expands to {total:,} bytes, over the {MAX_UNZIPPED:,} limit")
    return z


def _read(z: zipfile.ZipFile, name: str) -> bytes:
    try:
        with z.open(name) as f:
            return f.read()
    except KeyError:
        raise DocumentError(f"the document is missing {name}; it may be corrupt or encrypted") from None


# --------------------------------------------------------------------- DOCX
def _docx(z: zipfile.ZipFile) -> str:
    root = _xml_root(_read(z, "word/document.xml"))
    lines = []
    for para in root.iter():
        if _local(para.tag) != "p":
            continue
        parts = []
        for node in para.iter():
            name = _local(node.tag)
            if name == "t" and node.text:
                parts.append(node.text)
            elif name == "tab":
                parts.append("\t")
            elif name == "br":
                parts.append("\n")
        lines.append("".join(parts))
    return "\n".join(lines)


# --------------------------------------------------------------------- PPTX
def _pptx(z: zipfile.ZipFile) -> str:
    slides = sorted((n for n in z.namelist() if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                    key=lambda n: int(re.search(r"(\d+)", n).group(1)))
    out = []
    for name in slides:
        root = _xml_root(_read(z, name))
        lines = []
        for para in root.iter():
            if _local(para.tag) != "p":
                continue
            text = "".join(node.text for node in para.iter() if _local(node.tag) == "t" and node.text)
            if text:
                lines.append(text)
        if lines:
            out.append("\n".join(lines))
    return "\n\n".join(out)


# --------------------------------------------------------------------- XLSX
def _xlsx(z: zipfile.ZipFile) -> str:
    shared = []
    if "xl/sharedStrings.xml" in z.namelist():
        root = _xml_root(_read(z, "xl/sharedStrings.xml"))
        for si in root:
            if _local(si.tag) == "si":
                shared.append("".join(t.text for t in si.iter() if _local(t.tag) == "t" and t.text))
    sheets = sorted(n for n in z.namelist() if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", n))
    out = []
    for name in sheets:
        root = _xml_root(_read(z, name))
        rows = []
        for row in root.iter():
            if _local(row.tag) != "row":
                continue
            cells = []
            for c in row:
                if _local(c.tag) != "c":
                    continue
                v = next((e for e in c if _local(e.tag) in ("v", "t")), None)
                val = v.text if v is not None and v.text else ""
                if c.get("t") == "s" and val.isdigit() and int(val) < len(shared):
                    val = shared[int(val)]
                cells.append(val)
            if any(cells):
                rows.append("\t".join(cells))
        if rows:
            out.append("\n".join(rows))
    return "\n\n".join(out)


# --------------------------------------------------------------------- EPUB
def _strip_html(markup: bytes) -> str:
    from .web import parse_html

    return parse_html(markup.decode("utf-8", "replace")).block_text()


def _epub(z: zipfile.ZipFile) -> str:
    order = []
    try:  # follow the spine, so chapters come out in reading order
        container = _xml_root(_read(z, "META-INF/container.xml"))
        opf_path = next(e.get("full-path") for e in container.iter() if _local(e.tag) == "rootfile")
        opf = _xml_root(_read(z, opf_path))
        base = os.path.dirname(opf_path)
        manifest = {e.get("id"): e.get("href") for e in opf.iter() if _local(e.tag) == "item"}
        for ref in (e for e in opf.iter() if _local(e.tag) == "itemref"):
            href = manifest.get(ref.get("idref"))
            if href:
                order.append(os.path.normpath(os.path.join(base, href)).replace(os.sep, "/"))
    except (DocumentError, StopIteration):
        order = []
    if not order:  # fall back to every (x)html entry, in name order
        order = sorted(n for n in z.namelist() if n.lower().endswith((".xhtml", ".html", ".htm")))
    chapters = []
    for name in order:
        if name in z.namelist():
            text = _strip_html(_read(z, name)).strip()
            if text:
                chapters.append(text)
    return "\n\n".join(chapters)


# -------------------------------------------------------- OpenDocument (ODF)
def _odf(z: zipfile.ZipFile) -> str:
    root = _xml_root(_read(z, "content.xml"))
    lines, cells = [], []

    def walk(node, in_cell):
        name = _local(node.tag)
        cell = in_cell or name == "table-cell"
        buf = []
        if node.text:
            buf.append(node.text)
        for child in node:
            buf.append(walk(child, cell))
            if child.tail:
                buf.append(child.tail)
        text = "".join(buf)
        if name in ("p", "h"):  # a paragraph or heading ends a line (inside a cell it stays part of the cell)
            if in_cell:
                return text
            lines.append(text)
            return ""
        if name == "table-cell":
            cells.append(text)
            return ""
        if name == "table-row":
            if cells:
                lines.append("\t".join(cells))
                cells.clear()
            return ""
        return text

    walk(root, False)
    if cells:
        lines.append("\t".join(cells))
    return "\n".join(line for line in lines if line.strip())


# --------------------------------------------------------------------- RTF
_RTF_UNICODE = re.compile(r"\\u(-?\d+)\s?\??")
_RTF_HEX = re.compile(r"\\'([0-9a-fA-F]{2})")
_RTF_WORD = re.compile(r"\\([a-z]+)(-?\d+)? ?")
_RTF_SKIP_GROUPS = ("fonttbl", "colortbl", "stylesheet", "info", "pict", "object", "themedata",
                    "datastore", "generator")


def _rtf(data: bytes) -> str:
    text = data.decode("latin-1", "replace")
    # mark groups that are metadata or binary, e.g. {\fonttbl ...}, so their contents are dropped
    for word in _RTF_SKIP_GROUPS:
        text = re.sub(r"\{\\" + word + r"\b", lambda m: "{\\zzskip ", text)
    out, i, n, depth, skip_depth = [], 0, len(text), 0, None
    live = lambda: skip_depth is None  # noqa: E731  (True when text isn't inside a skipped group)
    while i < n:
        c = text[i]
        if c == "\\":
            m = _RTF_UNICODE.match(text, i)
            if m:
                if live():
                    out.append(chr(int(m.group(1)) % 0x110000))
                i = m.end()
                continue
            m = _RTF_HEX.match(text, i)
            if m:
                if live():
                    out.append(bytes([int(m.group(1), 16)]).decode("latin-1"))
                i = m.end()
                continue
            m = _RTF_WORD.match(text, i)
            if m:
                if m.group(1) == "zzskip":
                    skip_depth = depth
                elif live():
                    out.append({"par": "\n", "line": "\n", "tab": "\t"}.get(m.group(1), ""))
                i = m.end()
                continue
            if text[i + 1:i + 2] in ("\\", "{", "}"):  # an escaped literal \{ \} \\
                if live():
                    out.append(text[i + 1])
                i += 2
                continue
            i += 1
        elif c == "{":
            depth += 1
            i += 1
        elif c == "}":
            depth -= 1
            if skip_depth is not None and depth < skip_depth:  # left the skipped group
                skip_depth = None
            i += 1
        else:
            if live():
                out.append(c)
            i += 1
    return re.sub(r"[ \t]+\n", "\n", "".join(out)).strip()


# ----------------------------------------------------------------- dispatch
def extract(path: str | None = None, data: bytes | None = None, ext: str | None = None) -> str:
    """Plain text from a document, given a file path or its bytes. ``ext`` (``.docx`` …) overrides the
    extension from ``path``. Raises :class:`DocumentError` for an unsupported or unreadable document."""
    if ext is None and path is not None:
        ext = os.path.splitext(path)[1]
    ext = (ext or "").lower()
    if ext not in DOC_EXT:
        raise DocumentError(f"not a supported document format: {ext or '(unknown)'}")
    if data is None:
        with open(path, "rb") as f:  # type: ignore[arg-type]
            data = f.read()
    if ext == ".rtf":
        return _rtf(data)
    z = _open_zip(None if data is not None else path, data)
    try:
        if ext == ".docx":
            return _docx(z)
        if ext == ".pptx":
            return _pptx(z)
        if ext == ".xlsx":
            return _xlsx(z)
        if ext == ".epub":
            return _epub(z)
        return _odf(z)  # .odt / .odp / .ods
    finally:
        z.close()
