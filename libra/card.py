"""Render a Fandom-style *card* (an infobox) as a self-contained SVG, using only the standard library.

Libra has no image model — it cannot draw or generate a picture of a ship or a person. What it can do is
lay out the *structured* data (a spec sheet, a character sheet, a progression timeline) as a clean card
image: a titled header, an optional artwork slot (a picture you supply, embedded; otherwise a labelled
placeholder), rows of label/value fields grouped into sections, and a small footer. The result is a
complete ``<svg>`` document — open it in any browser, drop it in a page, or convert it to PNG.

The card *model* is a plain dict::

    {"title": str, "subtitle": str, "accent": "#hex" | None,
     "image": {"href": "<data: URI or URL>", "caption": str} | None,
     "sections": [{"heading": str, "rows": [(label, value), …]}, …],
     "footer": [str, …]}
"""
from __future__ import annotations

import base64
import mimetypes
import os
from xml.sax.saxutils import escape

W = 360                      # card width in px
PAD = 14
LABEL_W = 120                # width of the label column
GAP = 10
ACCENT = "#2b3a57"           # default header colour
VALUE_W = W - 2 * PAD - LABEL_W - GAP
_CHAR = 0.55                 # average glyph width as a fraction of font size (sans-serif estimate)


def _wrap(text: str, size: int, width_px: int) -> list:
    """Greedy word-wrap to a pixel width, hard-splitting any over-long word."""
    max_chars = max(4, int(width_px / (size * _CHAR)))
    out, line = [], ""
    for word in str(text).split():
        while len(word) > max_chars:                       # a single word longer than the column
            if line:
                out.append(line)
                line = ""
            out.append(word[:max_chars - 1] + "-")
            word = word[max_chars - 1:]
        cand = f"{line} {word}".strip()
        if len(cand) > max_chars and line:
            out.append(line)
            line = word
        else:
            line = cand
    if line:
        out.append(line)
    return out or [""]


def image_data_uri(path: str) -> str:
    """A ``data:`` URI for a local image file, to embed it in the card."""
    path = os.path.expanduser(path)
    mime = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        return f"data:{mime};base64," + base64.b64encode(f.read()).decode("ascii")


def _text(x, y, s, size, color, weight="normal", anchor="start"):
    w = ' font-weight="bold"' if weight == "bold" else ""
    a = f' text-anchor="{anchor}"' if anchor != "start" else ""
    return (f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}"{w}{a}>{escape(str(s))}</text>')


def render_card(model: dict) -> str:
    accent = model.get("accent") or ACCENT
    parts, y = [], 0

    # ---- header
    title_lines = _wrap(model.get("title", "Card"), 19, W - 2 * PAD)
    subtitle = model.get("subtitle", "")
    head_h = 14 + len(title_lines) * 24 + (16 if subtitle else 6)
    header = [f'<rect x="0" y="0" width="{W}" height="{head_h}" fill="{accent}"/>']
    ty = 30
    for ln in title_lines:
        header.append(_text(PAD, ty, ln, 19, "#ffffff", "bold"))
        ty += 24
    if subtitle:
        header.append(_text(PAD, ty, subtitle, 12, "#d7deea"))
    y = head_h

    # ---- artwork slot
    img = model.get("image")
    if img and img.get("href"):
        ih = 200
        parts.append(f'<image x="0" y="{y}" width="{W}" height="{ih}" href="{escape(img["href"])}" '
                     f'preserveAspectRatio="xMidYMid slice"/>')
        y += ih
        if img.get("caption"):
            parts.append(f'<rect x="0" y="{y}" width="{W}" height="20" fill="#000000" opacity="0.55"/>')
            parts.append(_text(W / 2, y + 14, img["caption"], 10, "#ffffff", anchor="middle"))
            y += 20
    else:
        ih = 92
        parts.append(f'<rect x="0" y="{y}" width="{W}" height="{ih}" fill="#eef1f5"/>')
        parts.append(_text(W / 2, y + ih / 2 - 4, "no illustration", 13, "#8a93a3", anchor="middle"))
        parts.append(_text(W / 2, y + ih / 2 + 14, "(Libra has no image model — add art with --image)",
                           9, "#aab2bf", anchor="middle"))
        y += ih

    # ---- field rows, grouped into sections
    row_i = 0
    for section in model.get("sections", []):
        heading = section.get("heading")
        if heading:
            parts.append(f'<rect x="0" y="{y}" width="{W}" height="24" fill="{accent}" opacity="0.12"/>')
            parts.append(_text(PAD, y + 16, heading, 12, accent, "bold"))
            y += 24
        for label, value in section.get("rows", []):
            vlines = _wrap(value, 12, VALUE_W)
            llines = _wrap(label, 12, LABEL_W)
            rows = max(len(vlines), len(llines))
            rh = rows * 16 + 10
            if row_i % 2 == 0:
                parts.append(f'<rect x="0" y="{y}" width="{W}" height="{rh}" fill="#f5f7fa"/>')
            ly = y + 16
            for i, ln in enumerate(llines):
                parts.append(_text(PAD, ly + i * 16, ln, 12, "#3a414d", "bold"))
            for i, ln in enumerate(vlines):
                parts.append(_text(PAD + LABEL_W + GAP, ly + i * 16, ln, 12, "#1c2127"))
            y += rh
            row_i += 1

    # ---- footer
    for line in model.get("footer", []):
        for ln in _wrap(line, 10, W - 2 * PAD):
            y += 14
            parts.append(_text(PAD, y, ln, 10, "#8a93a3"))
    y += PAD

    svg = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{y}" viewBox="0 0 {W} {y}" '
           f'font-family="Segoe UI, Helvetica, Arial, sans-serif">',
           f'<rect x="0" y="0" width="{W}" height="{y}" fill="#ffffff"/>',
           *parts, *header,
           f'<rect x="0.5" y="0.5" width="{W - 1}" height="{y - 1}" fill="none" stroke="#c7ceda"/>',
           "</svg>"]
    return "\n".join(svg)


# ---------------------------------------------------- record -> card model
def card_model(record: dict, image: str = "") -> dict:
    """Turn a worldbook / fic_spec / fic_progression record into a card model."""
    img = {"href": image_data_uri(image), "caption": ""} if image else None

    if "specs" in record:                                  # fic_spec spec sheet
        rows = list(record["specs"].items())
        sections = [{"heading": "Specifications", "rows": rows}]
        if record.get("mentions"):
            sections.append({"heading": "References", "rows": [("Tech / factions", ", ".join(record["mentions"]))]})
        if record.get("canon"):
            sections.append({"heading": "Canon cross-reference",
                             "rows": [(t, g.get("summary", "")) for t, g in record["canon"].items()]})
        return {"title": record.get("subject", "Spec sheet"), "subtitle": record.get("heading", "codex spec sheet"),
                "image": img, "sections": sections, "footer": [f"Source: {record.get('url', '')}"]}

    if "timeline" in record:                               # fic_progression evolution
        rows = [((e.get("at") or f"#{i + 1}"), e.get("text", "")) for i, e in enumerate(record["timeline"])]
        return {"title": record.get("character", "Character"), "subtitle": "capabilities gained",
                "image": img, "sections": [{"heading": "Evolution", "rows": rows}],
                "footer": [f"Source: {record.get('url', '')}"]}

    # worldbook entry / generic {title, fields, summary, categories}
    fields = record.get("fields", {})
    sections = [{"heading": "", "rows": list(fields.items())}] if fields else []
    footer = []
    if record.get("summary"):
        footer.append(record["summary"][:240])
    if record.get("categories"):
        footer.append("Categories: " + ", ".join(record["categories"][:8]))
    if record.get("url"):
        footer.append(f"Source: {record['url']}")
    return {"title": record.get("title", "Card"), "subtitle": record.get("kind", ""),
            "image": img, "sections": sections, "footer": footer}
