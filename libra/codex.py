"""A lore manager and world engine — your own local wiki of continuities, entries and timelines.

The Fandom model turned inward: keep your own archive of a **canon** continuity plus any number of **AUs**
(alternate universes), each with its own entries. An entry (a character, location, faction, item, tech,
species or event) has a title, a type, infobox fields, a body, categories, an optional image, a date (for
timelines) and cross-world links to the same subject in other continuities. It lives under ``~/.libra/codex``
(override with ``LIBRA_HOME``) as plain JSON files you own, back up and edit. Libra pulls from it: search,
render a card, fold into a world file, build a static wiki, or add it to the knowledge base so chat cites it.

Layout on disk::

    ~/.libra/codex/continuities.json          # the list of continuities (one is the canon)
    ~/.libra/codex/entries/<continuity>/<slug>.json
    ~/.libra/codex/images/<continuity>/<slug>.<ext>

Nothing here reaches the network; it's your data on your disk.
"""
from __future__ import annotations

import datetime
import json
import os
import re
import shutil

from . import home

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp"}
CANON_SLUG = "main-canon"                                        # the default canon continuity
# Entry types for the typed navigation (Characters, Locations, Factions, Timelines …).
ENTRY_TYPES = ("character", "location", "faction", "species", "item", "tech", "event", "article")
TYPE_LABELS = {"character": "Characters", "location": "Locations", "faction": "Factions",
               "species": "Species", "item": "Items", "tech": "Technology", "event": "Events",
               "article": "Articles"}
_TYPE_HINTS = [
    ("character", ("character", "person", "hero", "villain", "npc", "protagonist", "crew", "individual")),
    ("location", ("location", "place", "planet", "city", "region", "realm", "building", "world", "country")),
    ("faction", ("faction", "organization", "organisation", "group", "government", "military", "guild",
                 "house", "clan", "order")),
    ("species", ("species", "race", "creature", "alien", "lifeform")),
    ("item", ("item", "artifact", "weapon", "object", "relic", "equipment")),
    ("tech", ("tech", "technolog", "device", "ship", "starship", "vehicle", "vessel", "gadget")),
    ("event", ("event", "battle", "war", "incident", "mission")),
]


def type_singular(etype: str) -> str:
    """A singular label for a type ('character' -> 'Character', 'tech' -> 'Technology')."""
    label = TYPE_LABELS.get(etype, "Article")
    if etype == "species" or not label.endswith("s"):
        return label
    return label[:-1]


def classify_type(kind: str) -> str:
    """Map a free-text kind ('ship', 'planet', 'villain') to a navigation type."""
    low = (kind or "").lower()
    for etype, words in _TYPE_HINTS:
        if any(w in low for w in words):
            return etype
    return "article"


def base() -> str:
    return home.path("codex")


def slugify(title: str) -> str:
    slug = re.sub(r"[_\W]+", "-", title.strip().lower()).strip("-")   # spaces and underscores both separate
    return slug or "entry"


# --------------------------------------------------------------- continuities
def _meta_path() -> str:
    return os.path.join(base(), "continuities.json")


def _entries_root() -> str:
    d = os.path.join(base(), "entries")
    os.makedirs(d, exist_ok=True)
    return d


def _migrate() -> None:
    """Move any legacy flat entries (entries/*.json) into the canon continuity, once."""
    root = _entries_root()
    legacy = [n for n in os.listdir(root) if n.endswith(".json") and os.path.isfile(os.path.join(root, n))]
    if not legacy:
        return
    dest = os.path.join(root, CANON_SLUG)
    os.makedirs(dest, exist_ok=True)
    for name in legacy:
        with open(os.path.join(root, name), encoding="utf-8") as f:
            entry = json.load(f)
        entry.setdefault("type", classify_type(entry.get("kind", "")))
        entry["continuity"] = CANON_SLUG
        with open(os.path.join(dest, name), "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
        os.remove(os.path.join(root, name))
    old_img = os.path.join(base(), "images")                    # move flat images under the canon folder
    if os.path.isdir(old_img):
        flat = [n for n in os.listdir(old_img) if os.path.isfile(os.path.join(old_img, n))]
        if flat:
            cdir = os.path.join(old_img, CANON_SLUG)
            os.makedirs(cdir, exist_ok=True)
            for name in flat:
                shutil.move(os.path.join(old_img, name), os.path.join(cdir, name))
            for entry in all_entries(CANON_SLUG):               # fix image paths
                if entry.get("image") and "/" not in entry["image"].split("images/", 1)[-1]:
                    entry["image"] = entry["image"].replace("images/", f"images/{CANON_SLUG}/", 1)
                    _write(entry)


def continuities() -> list:
    """All continuities (the canon first), ensuring a default 'Main Canon' exists."""
    try:
        with open(_meta_path(), encoding="utf-8") as f:
            data = json.load(f)
        conts = data.get("continuities", [])
    except (OSError, ValueError):
        conts = []
    if not any(c.get("slug") == CANON_SLUG for c in conts):
        conts.insert(0, {"slug": CANON_SLUG, "name": "Main Canon", "type": "canon",
                         "description": "The established, official continuity."})
        _save_continuities(conts)
    _migrate()
    conts.sort(key=lambda c: (c["type"] != "canon", c["name"].lower()))
    return conts


def _save_continuities(conts: list) -> None:
    os.makedirs(base(), exist_ok=True)
    with open(_meta_path(), "w", encoding="utf-8") as f:
        json.dump({"continuities": conts}, f, ensure_ascii=False, indent=2)


def continuity(name_or_slug: str) -> dict | None:
    s = slugify(name_or_slug)
    for c in continuities():
        if c["slug"] == s or c["name"].lower() == name_or_slug.strip().lower():
            return c
    return None


def resolve_continuity(name_or_slug: str) -> str:
    """The slug of an existing continuity, or the canon if blank/unknown."""
    if not name_or_slug:
        return CANON_SLUG
    c = continuity(name_or_slug)
    return c["slug"] if c else CANON_SLUG


def add_continuity(name: str, ctype: str = "au", description: str = "") -> dict:
    if ctype not in ("canon", "au"):
        raise ValueError("continuity type must be 'canon' or 'au'")
    conts = continuities()
    slug = slugify(name)
    existing = next((c for c in conts if c["slug"] == slug), None)
    if existing:
        existing.update({"name": name, "type": ctype, "description": description})
    else:
        conts.append({"slug": slug, "name": name, "type": ctype, "description": description})
    _save_continuities(conts)
    return next(c for c in conts if c["slug"] == slug)


# ----------------------------------------------------------------- entries
def _dir(cont: str) -> str:
    d = os.path.join(_entries_root(), cont)
    os.makedirs(d, exist_ok=True)
    return d


def _path(cont: str, slug: str) -> str:
    return os.path.join(_dir(cont), slug + ".json")


def _images_dir(cont: str) -> str:
    d = os.path.join(base(), "images", cont)
    os.makedirs(d, exist_ok=True)
    return d


def exists(slug: str, continuity: str = CANON_SLUG) -> bool:
    return os.path.isfile(_path(resolve_continuity(continuity), slug))


def _write(entry: dict) -> None:
    with open(_path(entry["continuity"], entry["slug"]), "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)


def save(title: str, kind: str = "", fields: dict | None = None, body: str = "",
         categories: list | None = None, image: str | None = None, slug: str = "",
         continuity: str = CANON_SLUG, source: str = "", links: list | None = None,
         type: str = "", date: str = "") -> dict:
    """Create or update an entry in a continuity. ``type`` (a navigation category) defaults from ``kind``.
    ``date`` places it on the timeline; ``links`` are 'continuity/slug' cross-world references."""
    cont = resolve_continuity(continuity)
    slug = slug or slugify(title)
    etype = type if type in ENTRY_TYPES else classify_type(type or kind)
    entry = get(slug, cont) or {"slug": slug, "continuity": cont}
    entry.update({"title": title, "kind": kind, "type": etype, "fields": dict(fields or {}), "body": body,
                  "categories": list(categories or []), "continuity": cont, "source": source,
                  "date": date, "links": list(links or entry.get("links", [])),
                  "updated": datetime.datetime.now().isoformat(timespec="seconds")})
    if image:
        image = os.path.expanduser(image)
        ext = os.path.splitext(image)[1].lower()
        if ext not in IMAGE_EXT:
            raise ValueError(f"{os.path.basename(image)} isn't a supported image ({', '.join(sorted(IMAGE_EXT))})")
        if not os.path.isfile(image):
            raise ValueError(f"no such image: {image}")
        shutil.copyfile(image, os.path.join(_images_dir(cont), slug + ext))
        entry["image"] = f"images/{cont}/{slug}{ext}"
    entry.setdefault("image", None)
    _write(entry)
    return entry


def get(name: str, continuity: str = CANON_SLUG) -> dict | None:
    """An entry by slug or title within a continuity."""
    cont = resolve_continuity(continuity)
    slug = slugify(name)
    if os.path.isfile(_path(cont, slug)):
        with open(_path(cont, slug), encoding="utf-8") as f:
            return json.load(f)
    for entry in all_entries(cont):
        if entry.get("title", "").lower() == name.strip().lower():
            return entry
    return None


def delete(name: str, continuity: str = CANON_SLUG) -> bool:
    entry = get(name, continuity)
    if not entry:
        return False
    os.remove(_path(entry["continuity"], entry["slug"]))
    if entry.get("image"):
        img = os.path.join(base(), entry["image"])
        if os.path.isfile(img):
            os.remove(img)
    return True


def all_entries(continuity: str | None = None) -> list:
    """Every entry, or every entry in one continuity."""
    conts = [resolve_continuity(continuity)] if continuity else [c["slug"] for c in continuities()]
    out = []
    for cont in conts:
        d = os.path.join(_entries_root(), cont)
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if name.endswith(".json"):
                with open(os.path.join(d, name), encoding="utf-8") as f:
                    try:
                        out.append(json.load(f))
                    except json.JSONDecodeError:
                        continue
    return out


def _haystack(entry: dict) -> str:
    return " ".join([entry.get("title", ""), entry.get("kind", ""), entry.get("type", ""),
                     entry.get("body", ""), " ".join(entry.get("categories", [])),
                     " ".join(f"{k} {v}" for k, v in entry.get("fields", {}).items())]).lower()


def search(query: str, limit: int = 8, continuity: str | None = None, type: str = "") -> list:
    """Entries ranked by query-term overlap, optionally within a continuity and/or of one type."""
    pool = [e for e in all_entries(continuity) if not type or e.get("type") == type]
    terms = [t for t in re.split(r"\s+", query.lower().strip()) if t]
    if not terms:
        return pool[:limit]
    scored = []
    for entry in pool:
        hay = _haystack(entry)
        title = (entry.get("title", "") + " " + " ".join(entry.get("categories", []))).lower()
        score = sum(hay.count(t) for t in terms) + 3 * sum(t in title for t in terms)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda s: (-s[0], s[1].get("title", "")))
    return [e for _, e in scored[:limit]]


def by_type(continuity: str = CANON_SLUG) -> dict:
    """Entries of a continuity grouped by type, in ENTRY_TYPES order."""
    groups: dict = {}
    for entry in all_entries(continuity):
        groups.setdefault(entry.get("type", "article"), []).append(entry)
    return {t: groups[t] for t in ENTRY_TYPES if t in groups}


def timeline(continuity: str = CANON_SLUG) -> list:
    """Entries of a continuity that have a date, in chronological order."""
    dated = [e for e in all_entries(continuity) if e.get("date")]
    return sorted(dated, key=lambda e: (e["date"], e.get("title", "")))


def conflicts(existing: dict, new_fields: dict) -> dict:
    """Fields whose stored value would change — a change to established lore. ``{key: (old, new)}``."""
    old = existing.get("fields", {})
    return {k: (old[k], new_fields[k]) for k in new_fields if k in old and str(old[k]) != str(new_fields[k])}


def links_of(entry: dict) -> list:
    """Cross-world connections for an entry: its explicit ``links`` plus the same subject in other
    continuities (auto). Returns a list of (continuity dict, entry dict)."""
    out, seen = [], {(entry["continuity"], entry["slug"])}
    for ref in entry.get("links", []):
        cont, _, slug = ref.partition("/")
        target = get(slug, cont) if slug else None
        if target and (cont, slug) not in seen:
            seen.add((cont, slug))
            out.append((continuity(cont), target))
    for c in continuities():                                    # same slug in another continuity
        if (c["slug"], entry["slug"]) not in seen and os.path.isfile(_path(c["slug"], entry["slug"])):
            seen.add((c["slug"], entry["slug"]))
            out.append((c, get(entry["slug"], c["slug"])))
    return [(c, e) for c, e in out if c and e]


def image_path(entry: dict) -> str | None:
    if entry.get("image"):
        p = os.path.join(base(), entry["image"])
        return p if os.path.isfile(p) else None
    return None


def to_record(entry: dict) -> dict:
    """A card/worldbook-shaped record (title, kind, fields, summary, categories)."""
    return {"title": entry.get("title", ""), "kind": entry.get("kind") or entry.get("type", ""),
            "fields": entry.get("fields", {}), "summary": entry.get("body", ""),
            "categories": entry.get("categories", []), "url": f"codex:{entry.get('continuity', '')}/{entry.get('slug', '')}"}


def to_markdown(entry: dict) -> str:
    """An entry as Markdown, for the knowledge base to ingest and cite."""
    cont = continuity(entry.get("continuity", CANON_SLUG))
    lines = [f"# {entry.get('title', '')}"]
    sub = " · ".join(x for x in (entry.get("kind") or entry.get("type"), cont["name"] if cont else "") if x)
    if sub:
        lines.append(f"*{sub}*")
    if entry.get("date"):
        lines.append(f"Date: {entry['date']}")
    if entry.get("body"):
        lines += ["", entry["body"]]
    if entry.get("fields"):
        lines += [""] + [f"- **{k}:** {v}" for k, v in entry["fields"].items()]
    if entry.get("categories"):
        lines += ["", "Categories: " + ", ".join(entry["categories"])]
    if entry.get("source"):
        lines += ["", f"Source: {entry['source']}"]
    return "\n".join(lines)
