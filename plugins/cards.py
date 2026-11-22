"""Libra plugin: render a Fandom-style *card* (spec card, character card) as an image.

    libra skill run worldbook https://stargate.fandom.com/wiki/Thor --out thor.json
    libra skill run card thor.json --out thor.png            # character card PNG, from the wiki data
    libra skill run card https://stargate.fandom.com/wiki/Daedalus --out daedalus.png   # straight from a URL
    libra skill run card ragnarok.json --out ragnarok.svg --image ragnarok_art.png      # SVG + your art

It reads a record saved by ``worldbook``, ``fic_spec`` or ``fic_progression`` (or pulls a wiki page itself)
and lays it out as a clean card — a titled header, the label/value fields, categories/source, and an
artwork slot. A ``.png`` is rendered **by Libra itself** (``libra.raster``: from-scratch PNG, no external
tool); a ``.svg`` is vector and can embed real art. **Libra has no image model, so it does not draw the
ship or person** — the slot is a labelled placeholder unless you embed your own art with ``--image``.

Load with ``--plugins plugins/`` and accept once: ``libra trust accept-plugin plugins/cards.py``.
"""
from __future__ import annotations

import json
import os
import re

from libra import card as _card
from libra import wiki as _wiki
from libra.skills import SkillResult, fetcher, skill


def _wiki_record(url: str, fetch) -> dict | None:
    for api, title in _wiki.wiki_targets(url):
        parse = _wiki.api_parse(api, title, fetch)
        if parse is None:
            continue
        wt = parse["wikitext"] if isinstance(parse["wikitext"], str) else parse["wikitext"].get("*", "")
        name, fields = _wiki.infobox_fields(wt)
        cats = [c.get("category", "") if isinstance(c, dict) else str(c) for c in parse.get("categories", [])]
        cats = [c.replace("_", " ") for c in cats if c and not c.lower().startswith(("hidden", "stub"))]
        disp = re.sub(r"<[^>]+>", "", str(parse.get("displaytitle") or parse.get("title") or title))
        kind = re.sub(r"(?i)\binfobox\b", "", name).strip().title()
        return {"title": disp, "url": url, "kind": kind, "summary": _wiki.lead_summary(wt),
                "fields": fields, "categories": cats}
    return None


def _load_record(src: str) -> dict:
    src = os.path.expanduser(src)
    if re.match(r"https?://", src, re.I):
        rec = _wiki_record(src, fetcher().get)
        if rec is None or not rec["fields"] and not rec["summary"]:
            raise ValueError(f"couldn't read a wiki page at {src}. Save a record with another skill's --out "
                             "and pass the .json instead.")
        return rec
    if not os.path.isfile(src):
        raise ValueError(f"no such file: {src} (pass a .json saved by worldbook/fic_spec/fic_progression, "
                         "or a wiki page URL)")
    with open(src, encoding="utf-8") as f:
        rec = json.load(f)
    if isinstance(rec, dict) and isinstance(rec.get("world"), list) and rec["world"]:
        return rec["world"][0]                                  # a world file: card its first entry
    if not isinstance(rec, dict):
        raise ValueError(f"{src} isn't a card record (expected a JSON object)")
    return rec


@skill("card", "Render a Fandom-style spec/character card as an image (PNG or SVG) from a saved record or a "
       "wiki page. Libra renders the PNG itself — no external tool — but has no image model, so artwork is a "
       "placeholder unless you embed one with --image (SVG only).",
       {"src": "a .json from worldbook/fic_spec/fic_progression, or a wiki page URL",
        "out": "image file to write (.png rendered natively, or .svg)", "image": "image file to embed (SVG)",
        "scale": "PNG pixel scale, 1-4 (default 2)"},
       risk="read", external=True)
def card(src: str, out: str = "", image: str = "", scale: int = 2) -> SkillResult:
    record = _load_record(src)
    model = _card.card_model(record, image=image)

    if not out:
        slug = re.sub(r"[^\w-]+", "-", model["title"].lower()).strip("-") or "card"
        out = slug + ".png"
    out = os.path.expanduser(out)
    if not out.lower().endswith((".png", ".svg")):
        out += ".png"

    if out.lower().endswith(".png"):
        png = _card.render_card_png(model, scale=max(1, min(int(scale), 4)))
        with open(out, "wb") as f:
            f.write(png)
        detail, size = "rendered natively to PNG", len(png)
        note = "  [artwork is a placeholder — Libra can't draw it; embed real art with --image into an .svg]"
    else:
        svg = _card.render_card(model)
        with open(out, "w", encoding="utf-8") as f:
            f.write(svg)
        detail, size = "SVG", len(svg)
        note = "" if image else "  [artwork is a placeholder — embed real art with --image]"

    rows = sum(len(s.get("rows", [])) for s in model["sections"])
    return SkillResult("card", True, f"Wrote {out} ({detail}) — “{model['title']}” card with {rows} field(s)."
                       + note, {"file": out, "title": model["title"], "fields": rows, "bytes": size})
