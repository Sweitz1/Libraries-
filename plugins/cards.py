"""Libra plugin: render a Fandom-style *card* (spec card, character card) as an image.

    libra skill run worldbook https://stargate.fandom.com/wiki/Thor --out thor.json
    libra skill run card thor.json --out thor.svg            # character card, from the wiki data
    libra skill run card https://stargate.fandom.com/wiki/Daedalus --out daedalus.svg   # straight from a URL
    libra skill run card ragnarok.json --out ragnarok.svg --image ragnarok_art.png      # spec card + your art

It reads a record saved by ``worldbook``, ``fic_spec`` or ``fic_progression`` (or pulls a wiki page itself)
and lays it out as a clean SVG card — a titled header, the label/value fields, categories/source, and an
artwork slot. **Libra has no image model, so it does not draw the ship or person**; the slot shows a
labelled placeholder unless you embed real art with ``--image``. An SVG opens in any browser; pass an
``--out`` ending in ``.png`` and it is converted too if ``rsvg-convert``/``inkscape`` is installed.

Load with ``--plugins plugins/`` and accept once: ``libra trust accept-plugin plugins/cards.py``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess

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


def _to_png(svg: str, out: str) -> str | None:
    """Convert SVG to PNG if a converter is installed; returns the PNG path or None."""
    with open(out[:-4] + ".svg", "w", encoding="utf-8") as f:
        f.write(svg)
    svg_path = out[:-4] + ".svg"
    if shutil.which("rsvg-convert"):
        cmd = ["rsvg-convert", svg_path, "-o", out]
    elif shutil.which("inkscape"):
        cmd = ["inkscape", svg_path, "--export-type=png", f"--export-filename={out}"]
    else:
        return None
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=60)
        return out if r.returncode == 0 and os.path.isfile(out) else None
    except (OSError, subprocess.TimeoutExpired):
        return None


@skill("card", "Render a Fandom-style spec/character card as an SVG image from a saved record or a wiki page "
       "(Libra has no image model, so artwork is a placeholder unless you embed one with --image).",
       {"src": "a .json from worldbook/fic_spec/fic_progression, or a wiki page URL",
        "out": "image file to write (.svg, or .png to also convert)", "image": "optional image file to embed"},
       risk="read", external=True)
def card(src: str, out: str = "", image: str = "") -> SkillResult:
    record = _load_record(src)
    model = _card.card_model(record, image=image)
    svg = _card.render_card(model)

    if not out:
        slug = re.sub(r"[^\w-]+", "-", model["title"].lower()).strip("-") or "card"
        out = slug + ".svg"
    out = os.path.expanduser(out)
    wrote = []
    if out.lower().endswith(".png"):
        png = _to_png(svg, out)
        wrote.append(out[:-4] + ".svg")
        if png:
            wrote.append(png)
        else:
            return SkillResult("card", True, f"Wrote {out[:-4] + '.svg'} (no rsvg-convert/inkscape for PNG; "
                               "the SVG opens in any browser).", {"svg": out[:-4] + ".svg", "title": model["title"]})
    else:
        if not out.lower().endswith(".svg"):
            out += ".svg"
        with open(out, "w", encoding="utf-8") as f:
            f.write(svg)
        wrote.append(out)

    rows = sum(len(s.get("rows", [])) for s in model["sections"])
    note = ("" if image else "  [artwork is a placeholder — Libra can't draw it; embed real art with --image]")
    return SkillResult("card", True, f"Wrote {', '.join(wrote)} — “{model['title']}” card with {rows} field(s)."
                       + note, {"files": wrote, "title": model["title"], "fields": rows, "svg_length": len(svg)})
