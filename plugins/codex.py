"""Libra plugin: your **personal fan wiki** — add entries and images, then have the AI pull from them.

Keep your own codex of characters, items, tech, ships and places under ``~/.libra/codex`` and let Libra
search it, render a card from it, or fold it into the knowledge base so chat cites it.

    libra skill run codex_add "USS Daedalus" --kind ship --image daedalus.png \
        --fields "class=BC-304; armament=Asgard beams, railguns; crew=200" \
        --body "Earth's second BC-304, assigned to Atlantis." --categories "Ships, Tau'ri"
    libra skill run codex_search "BC-304 battlecruiser"
    libra skill run codex_card "USS Daedalus" --out daedalus.svg      # a card, with your image embedded
    libra skill run codex_import ragnarok.json                        # import a worldbook/fic_spec record
    libra skill run codex_serve                                       # browse/add at http://127.0.0.1:8800/

The AI pulls from it several ways: ``codex_search`` (the agent queries your wiki), ``codex_card`` (render an
entry), the knowledge base (``codex_export`` writes Markdown for ``libra kb add``, after which ``libra ask``
cites your entries), and ``codex_serve`` — a local website to browse and add entries, with a
MediaWiki-compatible ``api.php`` so ``worldbook``/``world_build`` can read your codex like a Fandom wiki.
It's all local files you own; nothing leaves the machine. Accept once: ``libra trust accept-plugin
plugins/codex.py``.
"""
from __future__ import annotations

import json
import os
import re

from libra import card as _card
from libra import codex as _codex
from libra.skills import SkillResult, skill


def _parse_fields(text: str) -> dict:
    fields = {}
    for part in re.split(r"[;\n]", text):
        key, sep, val = part.partition("=")
        if sep and key.strip():
            fields[key.strip()] = val.strip()
    return fields


def _parse_list(text: str) -> list:
    return [t.strip() for t in re.split(r"[,;\n]", text) if t.strip()]


@skill("codex_add", "Add or update an entry in your personal fan wiki (character, item, tech, ship, place) "
       "with fields and an optional image.",
       {"title": "the entry title, e.g. USS Daedalus", "kind": "character/item/tech/ship/place/…",
        "fields": "infobox fields, 'key=value; key=value'", "body": "a description paragraph",
        "categories": "comma-separated categories", "image": "optional image file to attach"},
       risk="write", external=False)
def codex_add(title: str, kind: str = "", fields: str = "", body: str = "", categories: str = "",
              image: str = "") -> SkillResult:
    if not title.strip():
        raise ValueError("codex_add needs a title")
    entry = _codex.save(title.strip(), kind=kind.strip(), fields=_parse_fields(fields), body=body.strip(),
                        categories=_parse_list(categories), image=image or None)
    note = f" with image {os.path.basename(entry['image'])}" if entry.get("image") else ""
    return SkillResult("codex_add", True,
                       f"Saved “{entry['title']}” ({entry.get('kind') or 'entry'}) to your codex"
                       f" — {len(entry['fields'])} field(s){note}. Slug: {entry['slug']}.", entry)


@skill("codex_search", "Search your personal fan wiki for entries.",
       {"query": "what to look for", "limit": "most entries to return (default 5)"},
       risk="read", external=False)
def codex_search(query: str, limit: int = 5) -> SkillResult:
    hits = _codex.search(query, max(1, int(limit)))
    if not hits:
        return SkillResult("codex_search", False, f"No codex entries match “{query}”. Add one with codex_add.")
    lines = [f"{len(hits)} match(es) for “{query}”:"]
    for e in hits:
        kind = f" [{e['kind']}]" if e.get("kind") else ""
        snippet = (e.get("body") or ", ".join(f"{k}: {v}" for k, v in e.get("fields", {}).items()))[:120]
        lines.append(f"- **{e['title']}**{kind}: {snippet}")
    return SkillResult("codex_search", True, "\n".join(lines),
                       {"query": query, "count": len(hits), "entries": hits})


@skill("codex_get", "Show one entry from your personal fan wiki.",
       {"name": "the entry's title or slug"}, risk="read", external=False)
def codex_get(name: str) -> SkillResult:
    entry = _codex.get(name)
    if entry is None:
        return SkillResult("codex_get", False, f"No codex entry called “{name}”.")
    return SkillResult("codex_get", True, _codex.to_markdown(entry), entry)


@skill("codex_card", "Render a card image for one of your personal-wiki entries (embeds its image in SVG).",
       {"name": "the entry's title or slug", "out": "image file (.svg to embed the image, or .png)",
        "image": "override the entry's image"}, risk="read", external=False)
def codex_card(name: str, out: str = "", image: str = "") -> SkillResult:
    entry = _codex.get(name)
    if entry is None:
        return SkillResult("codex_card", False, f"No codex entry called “{name}”.")
    img = image or _codex.image_path(entry) or ""
    model = _card.card_model(_codex.to_record(entry), image=img)
    if not out:
        out = entry["slug"] + (".svg" if img else ".png")      # SVG so the image can be embedded
    out = os.path.expanduser(out)
    if out.lower().endswith(".svg"):
        with open(out, "w", encoding="utf-8") as f:
            f.write(_card.render_card(model))
        extra = " (image embedded)" if img else ""
    else:
        if not out.lower().endswith(".png"):
            out += ".png"
        with open(out, "wb") as f:
            f.write(_card.render_card_png(model))
        extra = " (PNG can't embed the image — use .svg for that)" if img else ""
    return SkillResult("codex_card", True, f"Wrote {out}{extra}.", {"file": out, "title": entry["title"]})


def _as_entries(record: dict) -> list:
    """Map a worldbook / fic_spec / fic_progression / world record to codex entry kwargs."""
    if isinstance(record.get("world"), list):                  # a world file: many entries
        return [kw for e in record["world"] for kw in _as_entries(e)]
    if "specs" in record:                                      # fic_spec
        body = record.get("heading", "")
        if record.get("mentions"):
            body = (body + "\nReferences: " + ", ".join(record["mentions"])).strip()
        return [{"title": record.get("subject", "Spec"), "kind": "ship", "fields": record["specs"],
                 "body": body, "categories": record.get("mentions", [])}]
    if "timeline" in record:                                   # fic_progression
        body = "\n".join(e.get("text", "") for e in record["timeline"])
        return [{"title": record.get("character", "Character"), "kind": "character", "fields": {},
                 "body": body, "categories": ["progression"]}]
    if record.get("title"):                                     # worldbook / generic
        return [{"title": record["title"], "kind": record.get("kind", ""), "fields": record.get("fields", {}),
                 "body": record.get("summary", ""), "categories": record.get("categories", [])}]
    return []


@skill("codex_import", "Import a saved record (worldbook / fic_spec / fic_progression / world file) into "
       "your personal fan wiki as one or more entries.",
       {"src": "the .json file to import"}, risk="write", external=False)
def codex_import(src: str) -> SkillResult:
    src = os.path.expanduser(src)
    if not os.path.isfile(src):
        raise ValueError(f"no such file: {src}")
    with open(src, encoding="utf-8") as f:
        record = json.load(f)
    entries = _as_entries(record) if isinstance(record, dict) else []
    if not entries:
        return SkillResult("codex_import", False, f"Didn't recognise any entries in {src}.")
    saved = [_codex.save(**kw) for kw in entries]
    return SkillResult("codex_import", True,
                       f"Imported {len(saved)} entr{'y' if len(saved) == 1 else 'ies'} into your codex: "
                       + ", ".join(e["title"] for e in saved[:10]) + ("…" if len(saved) > 10 else ""),
                       {"count": len(saved), "titles": [e["title"] for e in saved]})


@skill("codex_serve", "Serve your personal fan wiki as a local website (browse and add entries) with a "
       "MediaWiki-compatible api.php that worldbook/world_build can read. Runs until you stop it (Ctrl-C).",
       {"host": "address to bind (default 127.0.0.1, local only)", "port": "port (default 8800)"},
       risk="write", external=False)
def codex_serve(host: str = "127.0.0.1", port: int = 8800) -> SkillResult:
    from libra.codexsite import make_server

    port = int(port)
    srv = make_server(host, port)
    url = f"http://{host}:{port}/"
    warn = "" if host in ("127.0.0.1", "localhost") else "  WARNING: bound to a non-local address — anyone " \
        "who can reach it can add/read entries (no auth)."
    print(f"Codex wiki at {url}  (API: {url}api.php). Ctrl-C to stop.{warn}")
    print(f"Point Libra at it:  libra --allow-private skill run worldbook {url}wiki/<Title>")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.shutdown()
        srv.server_close()
    return SkillResult("codex_serve", True, f"Codex site at {url} stopped.", {"url": url})


@skill("codex_export", "Export your personal fan wiki as Markdown files, so `libra kb add` can index and "
       "cite them in chat.",
       {"out_dir": "folder to write the .md files to (default ~/.libra/codex/markdown)"},
       risk="write", external=False)
def codex_export(out_dir: str = "") -> SkillResult:
    out_dir = os.path.expanduser(out_dir) if out_dir else os.path.join(_codex.base(), "markdown")
    os.makedirs(out_dir, exist_ok=True)
    entries = _codex.all_entries()
    for e in entries:
        with open(os.path.join(out_dir, e["slug"] + ".md"), "w", encoding="utf-8") as f:
            f.write(_codex.to_markdown(e) + "\n")
    return SkillResult("codex_export", True,
                       f"Wrote {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} to {out_dir}. "
                       f"Add them with: libra kb add {out_dir}", {"dir": out_dir, "count": len(entries)})
