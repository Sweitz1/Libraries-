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


@skill("codex_add", "Add or update an entry (character, location, faction, item, tech, species, event) in a "
       "continuity of your world. Flags contradictions before changing established lore (re-run with "
       "force=true to apply).",
       {"title": "the entry title, e.g. USS Daedalus", "kind": "free label, e.g. ship / planet / villain",
        "type": "navigation type: character/location/faction/species/item/tech/event/article (else from kind)",
        "fields": "infobox fields, 'key=value; key=value'", "body": "a description (use == Heading == sections)",
        "categories": "comma-separated categories", "image": "optional image file to attach",
        "continuity": "which continuity/AU (default Main Canon)", "date": "a date, to place it on the timeline",
        "links": "cross-world links 'continuity/slug, …' to the same subject elsewhere",
        "source": "where the lore came from", "force": "true to change an existing field value"},
       risk="write", external=False)
def codex_add(title: str, kind: str = "", type: str = "", fields: str = "", body: str = "",
              categories: str = "", image: str = "", continuity: str = "", date: str = "", links: str = "",
              source: str = "", force: bool = False) -> SkillResult:
    if not title.strip():
        raise ValueError("codex_add needs a title")
    cont = _codex.resolve_continuity(continuity)
    new_fields = _parse_fields(fields)
    existing = _codex.get(title.strip(), cont)
    if existing and not force:                                 # flag contradictions before changing established lore
        clashes = _codex.conflicts(existing, new_fields)
        if clashes:
            lines = [f"⚠ “{title.strip()}” already exists in {_cont_name(cont)} — this would change "
                     "established lore. Not saved."]
            lines += [f"  - {k}: “{old}” → “{new}”" for k, (old, new) in clashes.items()]
            lines.append("Re-run with force=true to apply, or add it to a different continuity (an AU) to "
                         "keep both versions separate.")
            return SkillResult("codex_add", False, "\n".join(lines),
                               {"conflicts": {k: {"old": o, "new": n} for k, (o, n) in clashes.items()},
                                "title": title.strip(), "continuity": cont})
    entry = _codex.save(title.strip(), kind=kind.strip(), type=type.strip(), fields=new_fields,
                        body=body.strip(), categories=_parse_list(categories), image=image or None,
                        continuity=cont, date=date.strip(), links=_parse_list(links), source=source.strip())
    note = f" with image {os.path.basename(entry['image'])}" if entry.get("image") else ""
    return SkillResult("codex_add", True,
                       f"Saved “{entry['title']}” ({_codex.TYPE_LABELS.get(entry['type'], entry['type'])[:-1]}"
                       f" in {_cont_name(cont)}) — {len(entry['fields'])} field(s){note}. Slug: {entry['slug']}.",
                       entry)


def _cont_name(slug: str) -> str:
    c = _codex.continuity(slug)
    return c["name"] if c else slug


@skill("codex_search", "Search your world's entries, optionally within one continuity and/or of one type.",
       {"query": "what to look for", "continuity": "limit to a continuity/AU (default: all)",
        "type": "limit to a type (character/location/faction/…)", "limit": "most entries (default 5)"},
       risk="read", external=False)
def codex_search(query: str, continuity: str = "", type: str = "", limit: int = 5) -> SkillResult:
    cont = _codex.resolve_continuity(continuity) if continuity else None
    hits = _codex.search(query, max(1, int(limit)), continuity=cont, type=type.strip())
    if not hits:
        return SkillResult("codex_search", False, f"No entries match “{query}”. Add one with codex_add.")
    lines = [f"{len(hits)} match(es) for “{query}”:"]
    for e in hits:
        tags = f" [{_cont_name(e['continuity'])} · {e.get('type', 'article')}]"
        snippet = (e.get("body") or ", ".join(f"{k}: {v}" for k, v in e.get("fields", {}).items()))[:120]
        lines.append(f"- **{e['title']}**{tags}: {snippet}")
    return SkillResult("codex_search", True, "\n".join(lines),
                       {"query": query, "count": len(hits), "entries": hits})


@skill("codex_get", "Show one entry from your world.",
       {"name": "the entry's title or slug", "continuity": "which continuity/AU (default Main Canon)"},
       risk="read", external=False)
def codex_get(name: str, continuity: str = "") -> SkillResult:
    entry = _codex.get(name, _codex.resolve_continuity(continuity))
    if entry is None:
        return SkillResult("codex_get", False, f"No entry called “{name}” in {_cont_name(_codex.resolve_continuity(continuity))}.")
    text = _codex.to_markdown(entry)
    cross = _codex.links_of(entry)
    if cross:
        text += "\n\nAlso in: " + ", ".join(f"{e['title']} ({c['name']})" for c, e in cross)
    return SkillResult("codex_get", True, text, entry)


@skill("codex_card", "Render a card image for one of your entries (embeds its image in SVG).",
       {"name": "the entry's title or slug", "continuity": "which continuity/AU (default Main Canon)",
        "out": "image file (.svg to embed the image, or .png)", "image": "override the entry's image"},
       risk="read", external=False)
def codex_card(name: str, continuity: str = "", out: str = "", image: str = "") -> SkillResult:
    entry = _codex.get(name, _codex.resolve_continuity(continuity))
    if entry is None:
        return SkillResult("codex_card", False, f"No entry called “{name}”.")
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
    src = record.get("url", "")
    if "specs" in record:                                      # fic_spec
        body = record.get("heading", "")
        if record.get("mentions"):
            body = (body + "\nReferences: " + ", ".join(record["mentions"])).strip()
        return [{"title": record.get("subject", "Spec"), "kind": "ship", "fields": record["specs"],
                 "body": body, "categories": record.get("mentions", []), "source": src}]
    if "timeline" in record:                                   # fic_progression
        body = "\n".join(e.get("text", "") for e in record["timeline"])
        return [{"title": record.get("character", "Character"), "kind": "character", "fields": {},
                 "body": body, "categories": ["progression"], "source": src}]
    if record.get("title"):                                     # worldbook / generic
        return [{"title": record["title"], "kind": record.get("kind", ""), "fields": record.get("fields", {}),
                 "body": record.get("summary", ""), "categories": record.get("categories", []),
                 "source": record.get("url", "")}]
    return []


@skill("codex_import", "Import a saved record (worldbook / fic_spec / fic_progression / world file) into a "
       "continuity of your world as one or more entries.",
       {"src": "the .json file to import", "continuity": "which continuity/AU to import into (default Main "
        "Canon); name a new one to keep references separate (e.g. 'Sources')"}, risk="write", external=False)
def codex_import(src: str, continuity: str = "") -> SkillResult:
    src = os.path.expanduser(src)
    if not os.path.isfile(src):
        raise ValueError(f"no such file: {src}")
    cont = _codex.resolve_continuity(continuity)
    if continuity and not _codex.continuity(continuity):       # create the named continuity if new
        cont = _codex.add_continuity(continuity.strip(), ctype="au",
                                     description="Imported reference material")["slug"]
    with open(src, encoding="utf-8") as f:
        record = json.load(f)
    entries = _as_entries(record) if isinstance(record, dict) else []
    if not entries:
        return SkillResult("codex_import", False, f"Didn't recognise any entries in {src}.")
    saved = [_codex.save(continuity=cont, **kw) for kw in entries]
    return SkillResult("codex_import", True,
                       f"Imported {len(saved)} entr{'y' if len(saved) == 1 else 'ies'} into "
                       f"{_cont_name(cont)}: " + ", ".join(e["title"] for e in saved[:10])
                       + ("…" if len(saved) > 10 else ""),
                       {"count": len(saved), "continuity": cont, "titles": [e["title"] for e in saved]})


@skill("codex_continuity", "List your continuities, or add one (a canon or an AU — alternate universe).",
       {"name": "name of a continuity to add (leave blank to just list)",
        "type": "'canon' or 'au' (default au)", "description": "a one-line description"},
       risk="write", external=False)
def codex_continuity(name: str = "", type: str = "au", description: str = "") -> SkillResult:
    if name.strip():
        c = _codex.add_continuity(name.strip(), ctype=(type.strip().lower() or "au"), description=description.strip())
        verb = "Added"
    else:
        c = None
        verb = "Your"
    conts = _codex.continuities()
    lines = [f"{verb} continuit{'y' if c else 'ies'}:" if not c else f"Added continuity “{c['name']}” ({c['type']})."]
    for cc in conts:
        n = len(_codex.all_entries(cc["slug"]))
        lines.append(f"- **{cc['name']}** [{cc['type']}] — {n} entr{'y' if n == 1 else 'ies'}"
                     + (f": {cc['description']}" if cc.get("description") else ""))
    return SkillResult("codex_continuity", True, "\n".join(lines),
                       {"continuities": conts, "added": c})


@skill("codex_timeline", "Show a continuity's timeline — its dated entries in chronological order.",
       {"continuity": "which continuity/AU (default Main Canon)"}, risk="read", external=False)
def codex_timeline(continuity: str = "") -> SkillResult:
    cont = _codex.resolve_continuity(continuity)
    events = _codex.timeline(cont)
    if not events:
        return SkillResult("codex_timeline", False,
                           f"No dated entries in {_cont_name(cont)}. Add a `date` to entries to place them "
                           "on the timeline.")
    lines = [f"# {_cont_name(cont)} — timeline ({len(events)})"]
    for e in events:
        lines.append(f"- **{e['date']}** — {e['title']}"
                     + (f": {e['body'][:100]}" if e.get("body") else ""))
    return SkillResult("codex_timeline", True, "\n".join(lines), {"continuity": cont, "events": events})


@skill("codex_link", "Link the same subject across continuities (a cross-world connection).",
       {"name": "the entry to add a link to", "continuity": "its continuity (default Main Canon)",
        "to": "the linked entry as 'continuity/slug' (e.g. frostline-au/thor)"}, risk="write", external=False)
def codex_link(name: str, to: str, continuity: str = "") -> SkillResult:
    cont = _codex.resolve_continuity(continuity)
    entry = _codex.get(name, cont)
    if entry is None:
        return SkillResult("codex_link", False, f"No entry called “{name}” in {_cont_name(cont)}.")
    tcont, _, tslug = to.strip().partition("/")
    if not _codex.get(tslug, tcont):
        return SkillResult("codex_link", False, f"No entry at {to}. Use 'continuity-slug/entry-slug'.")
    links = list(dict.fromkeys(entry.get("links", []) + [f"{_codex.resolve_continuity(tcont)}/{tslug}"]))
    saved = _codex.save(entry["title"], kind=entry.get("kind", ""), type=entry.get("type", ""),
                        fields=entry.get("fields", {}), body=entry.get("body", ""),
                        categories=entry.get("categories", []), continuity=cont, date=entry.get("date", ""),
                        links=links, source=entry.get("source", ""))
    return SkillResult("codex_link", True,
                       f"Linked “{saved['title']}” ({_cont_name(cont)}) to {to}. "
                       f"{len(_codex.links_of(saved))} cross-world connection(s).", saved)


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


@skill("codex_build", "Generate a static, text-only wiki-style website from your codex (the lorebook is the "
       "single source of truth, so the site never drifts from it). No server needed.",
       {"out_dir": "folder to write the site to (default ~/.libra/codex/site)"},
       risk="write", external=False)
def codex_build(out_dir: str = "") -> SkillResult:
    from libra.codexsite import build_site

    out_dir = os.path.expanduser(out_dir) if out_dir else os.path.join(_codex.base(), "site")
    report = build_site(out_dir)
    return SkillResult("codex_build", True,
                       f"Built a static wiki of {report['entries']} entr"
                       f"{'y' if report['entries'] == 1 else 'ies'} at {out_dir}. Open {report['index']} "
                       "in a browser, or host the folder anywhere (no server, no JavaScript).", report)


@skill("codex_export", "Export your personal fan wiki as Markdown files, so `libra kb add` can index and "
       "cite them in chat.",
       {"out_dir": "folder to write the .md files to (default ~/.libra/codex/markdown)"},
       risk="write", external=False)
def codex_export(out_dir: str = "") -> SkillResult:
    out_dir = os.path.expanduser(out_dir) if out_dir else os.path.join(_codex.base(), "markdown")
    os.makedirs(out_dir, exist_ok=True)
    entries = _codex.all_entries()
    for e in entries:                                          # continuity in the name so AUs don't collide
        name = (f"{e['continuity']}__{e['slug']}.md" if e.get("continuity") != _codex.CANON_SLUG
                else e["slug"] + ".md")
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as f:
            f.write(_codex.to_markdown(e) + "\n")
    return SkillResult("codex_export", True,
                       f"Wrote {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} to {out_dir}. "
                       f"Add them with: libra kb add {out_dir}", {"dir": out_dir, "count": len(entries)})
