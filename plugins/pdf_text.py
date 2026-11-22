"""Libra plugin: extract the text from a PDF, so it can go into your knowledge base.

    libra skill run pdf_text report.pdf --out report.txt
    libra kb add report.txt            # then it's searched and cited like any file

Libra has no PDF library (NumPy and the standard library only), so the extractor here is written from
scratch: it inflates the PDF's content streams with ``zlib`` and reads the text-showing operators. That
handles most text PDFs. For anything it can't (unusual fonts, odd encodings), it falls back to the system
``pdftotext`` (from poppler-utils) when that's installed. A scanned, image-only PDF has no text to pull out;
it needs OCR, which this plugin doesn't do, and says so.

Load it with ``--plugins plugins/`` (or copy to ``~/.libra/plugins/``). Like any plugin, its risk labels
are believed only after ``libra trust accept-plugin plugins/pdf_text.py``.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import zlib

from libra.skills import SkillResult, skill

_STREAM = re.compile(rb"stream\r?\n(.*?)\r?\n?endstream", re.S)
# octal escape \ddd and the named escapes inside a PDF literal string
_OCTAL = re.compile(rb"\\([0-7]{1,3})")
_ESCAPES = {b"n": b"\n", b"r": b"\r", b"t": b"\t", b"b": b"\b", b"f": b"\f",
            b"(": b"(", b")": b")", b"\\": b"\\"}


def _decode_literal(raw: bytes) -> str:
    """Decode a PDF literal string's bytes (the part between its parentheses)."""
    out = bytearray()
    i, n = 0, len(raw)
    while i < n:
        c = raw[i:i + 1]
        if c == b"\\" and i + 1 < n:
            nxt = raw[i + 1:i + 2]
            m = _OCTAL.match(raw, i + 1)
            if m:
                out.append(int(m.group(1), 8) & 0xFF)
                i = m.end()
                continue
            if nxt in (b"\n", b"\r"):  # line continuation: the newline is dropped
                i += 2
                continue
            out += _ESCAPES.get(nxt, nxt)
            i += 2
            continue
        out += c
        i += 1
    return out.decode("latin-1")


def _text_from_content(content: bytes) -> str:
    """Pull the shown text out of one decoded content stream, keeping rough line breaks."""
    lines, cur = [], []
    i, n = 0, len(content)
    while i < n:
        c = content[i:i + 1]
        if c == b"(":  # a literal string: read to the matching ), honouring escapes and nesting
            depth, j, esc = 1, i + 1, False
            while j < n and depth:
                ch = content[j:j + 1]
                if esc:
                    esc = False
                elif ch == b"\\":
                    esc = True
                elif ch == b"(":
                    depth += 1
                elif ch == b")":
                    depth -= 1
                j += 1
            cur.append(_decode_literal(content[i + 1:j - 1]))
            i = j
        elif c == b"<" and content[i + 1:i + 2] != b"<":  # a hex string <48656c6c6f>
            j = content.find(b">", i)
            if j == -1:
                break
            hx = re.sub(rb"\s", b"", content[i + 1:j])
            if len(hx) % 2:
                hx += b"0"
            try:
                cur.append(bytes.fromhex(hx.decode("ascii")).decode("latin-1"))
            except ValueError:
                pass
            i = j + 1
        elif c.isalpha() or c in (b"'", b'"'):
            j = i  # read a whole operator token (Td, TD, T*, Tj, ', " …)
            while j < n and content[j:j + 1] not in b" \t\r\n()<>[]{}/%":
                j += 1
            if content[i:j] in (b"Td", b"TD", b"T*", b"'", b'"'):  # these begin a new line of text
                if cur:
                    lines.append("".join(cur))
                    cur = []
            i = max(j, i + 1)
        else:
            i += 1
    if cur:
        lines.append("".join(cur))
    return "\n".join(line for line in lines if line.strip())


def _builtin_extract(data: bytes) -> str:
    """Extract text from a PDF using only the standard library."""
    out = []
    for m in _STREAM.finditer(data):
        raw = m.group(1)
        try:
            content = zlib.decompress(raw)
        except zlib.error:
            content = raw  # not FlateDecode (or already plain): try it as-is
        try:
            out.append(_text_from_content(content))
        except Exception:  # one unreadable stream shouldn't lose the rest
            continue
    return "\n".join(t for t in out if t.strip()).strip()


def _pdftotext(path: str, pages: str) -> str | None:
    """The system pdftotext, if installed. Returns None when it isn't."""
    exe = shutil.which("pdftotext")
    if not exe:
        return None
    cmd = [exe, "-q", "-enc", "UTF-8"]
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", pages or "")
    if m:
        cmd += ["-f", m.group(1), "-l", m.group(2)]
    cmd += [path, "-"]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout.decode("utf-8", "replace") if r.returncode == 0 else None


def _route(message: str):
    m = re.search(r"\b(?:extract text from|read|pdf to text|convert)\b.*?([^\s\"']+\.pdf)\b", message, re.I)
    return {"path": m.group(1)} if m else None


@skill("pdf_text", "Extract the text from a PDF file (so it can be added to your knowledge base).",
       {"path": "the .pdf file", "out": "optional .txt file to write the full text to",
        "pages": "optional page range for pdftotext, e.g. 1-5", "method": "auto, builtin or pdftotext"},
       router=_route, risk="read")
def pdf_text(path: str, out: str = "", pages: str = "", method: str = "auto") -> SkillResult:
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise ValueError(f"no such file: {path}")
    with open(path, "rb") as f:
        head = f.read(5)
        f.seek(0)
        data = f.read()
    if not head.startswith(b"%PDF"):
        raise ValueError(f"{os.path.basename(path)} doesn't look like a PDF (no %PDF header)")

    method = method.lower()
    text, used = "", ""
    if method in ("auto", "builtin"):
        text, used = _builtin_extract(data), "builtin"
    if method == "pdftotext" or (method == "auto" and len(text) < 20):
        viz = _pdftotext(path, pages)  # better fonts/encodings than the from-scratch reader
        if viz is not None and len(viz.strip()) >= len(text.strip()):
            text, used = viz.strip(), "pdftotext"

    if not text.strip():
        hint = "" if shutil.which("pdftotext") else " Install poppler-utils for pdftotext, or"
        return SkillResult("pdf_text", False,
                           f"No text found in {os.path.basename(path)}. It's probably a scanned (image-only) "
                           f"PDF, which needs OCR.{hint} describe it in a .txt file next to it instead.")

    if out:
        out = os.path.expanduser(out)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    shown = text if len(text) <= 4000 else text[:4000].rstrip() + f"\n… ({len(text) - 4000:,} more characters)"
    note = f"\n\n[{len(text):,} characters via {used}" + (f", written to {out}" if out else "") + "]"
    return SkillResult("pdf_text", True, shown + note, {"path": path, "characters": len(text), "method": used,
                                                        "out": out or None, "text": text})
