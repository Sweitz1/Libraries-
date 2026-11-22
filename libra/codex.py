"""A personal fan wiki — your own local "codex" of entries (characters, items, tech, ships, places).

This is the Fandom model turned inward: instead of only *reading* someone else's wiki, you keep your own
store of entries, each with a title, a kind, infobox-style fields, a body, categories and an optional
image. It lives under ``~/.libra/codex`` (override with ``LIBRA_HOME``) as one JSON file per entry plus an
``images/`` folder — plain files you own, back up and edit. Libra pulls from it: search it, render an entry
as a card, fold it into a world file, or add it to the knowledge base so chat cites it.

An entry is a plain dict::

    {"slug": str, "title": str, "kind": str, "fields": {label: value},
     "body": str, "categories": [str], "image": "images/<slug>.png" | None, "updated": iso8601}

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

# Canon tiers, kept separate so official lore, other authors' fanon and your own canon never blur together.
CANON_TIERS = {"official": "Official canon", "fanon": "Fanon (other authors)", "mine": "Your canon"}
DEFAULT_TIER = "mine"


def base() -> str:
    return home.path("codex")


def _entries_dir() -> str:
    d = os.path.join(base(), "entries")
    os.makedirs(d, exist_ok=True)
    return d


def _images_dir() -> str:
    d = os.path.join(base(), "images")
    os.makedirs(d, exist_ok=True)
    return d


def slugify(title: str) -> str:
    slug = re.sub(r"[_\W]+", "-", title.strip().lower()).strip("-")   # spaces and underscores both separate
    return slug or "entry"


def _path(slug: str) -> str:
    return os.path.join(_entries_dir(), slug + ".json")


def exists(slug: str) -> bool:
    return os.path.isfile(_path(slug))


def save(title: str, kind: str = "", fields: dict | None = None, body: str = "",
         categories: list | None = None, image: str | None = None, slug: str = "",
         canon: str = DEFAULT_TIER, source: str = "") -> dict:
    """Create or update an entry. ``image`` is a path to copy into the codex. ``canon`` is the tier
    (official / fanon / mine) and ``source`` where the lore came from. Returns the stored entry."""
    if canon not in CANON_TIERS:
        raise ValueError(f"canon must be one of {', '.join(CANON_TIERS)} (got {canon!r})")
    slug = slug or slugify(title)
    entry = get(slug) or {"slug": slug}
    entry.update({"title": title, "kind": kind, "fields": dict(fields or {}), "body": body,
                  "categories": list(categories or []), "canon": canon, "source": source,
                  "updated": datetime.datetime.now().isoformat(timespec="seconds")})
    if image:
        image = os.path.expanduser(image)
        ext = os.path.splitext(image)[1].lower()
        if ext not in IMAGE_EXT:
            raise ValueError(f"{os.path.basename(image)} isn't a supported image ({', '.join(sorted(IMAGE_EXT))})")
        if not os.path.isfile(image):
            raise ValueError(f"no such image: {image}")
        dest = os.path.join(_images_dir(), slug + ext)
        shutil.copyfile(image, dest)
        entry["image"] = os.path.join("images", slug + ext)
    entry.setdefault("image", None)
    with open(_path(slug), "w", encoding="utf-8") as f:
        json.dump(entry, f, ensure_ascii=False, indent=2)
    return entry


def get(name: str) -> dict | None:
    """An entry by slug or title."""
    slug = slugify(name)
    if exists(slug):
        with open(_path(slug), encoding="utf-8") as f:
            return json.load(f)
    for entry in all_entries():                                 # fall back to a title match
        if entry.get("title", "").lower() == name.strip().lower():
            return entry
    return None


def delete(name: str) -> bool:
    entry = get(name)
    if not entry:
        return False
    os.remove(_path(entry["slug"]))
    if entry.get("image"):
        img = os.path.join(base(), entry["image"])
        if os.path.isfile(img):
            os.remove(img)
    return True


def all_entries() -> list:
    out = []
    for name in sorted(os.listdir(_entries_dir())) if os.path.isdir(_entries_dir()) else []:
        if name.endswith(".json"):
            with open(os.path.join(_entries_dir(), name), encoding="utf-8") as f:
                try:
                    out.append(json.load(f))
                except json.JSONDecodeError:
                    continue
    return out


def _haystack(entry: dict) -> str:
    parts = [entry.get("title", ""), entry.get("kind", ""), entry.get("body", ""),
             " ".join(entry.get("categories", [])),
             " ".join(f"{k} {v}" for k, v in entry.get("fields", {}).items())]
    return " ".join(parts).lower()


def conflicts(existing: dict, new_fields: dict) -> dict:
    """Fields whose stored value would change — a change to established lore. ``{key: (old, new)}``."""
    old = existing.get("fields", {})
    return {k: (old[k], new_fields[k]) for k in new_fields if k in old and str(old[k]) != str(new_fields[k])}


def by_tier() -> dict:
    """All entries grouped by canon tier, in tier order (official, fanon, mine)."""
    groups: dict = {t: [] for t in CANON_TIERS}
    for entry in all_entries():
        groups.setdefault(entry.get("canon", DEFAULT_TIER), []).append(entry)
    return {t: v for t, v in groups.items() if v}


def search(query: str, limit: int = 8, tier: str = "") -> list:
    """Entries ranked by how many query terms they contain (title and categories weighted). ``tier``
    (official/fanon/mine), if given, keeps only that canon tier."""
    pool = [e for e in all_entries() if not tier or e.get("canon", DEFAULT_TIER) == tier]
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


def image_path(entry: dict) -> str | None:
    """Absolute path to an entry's image, if it has one."""
    if entry.get("image"):
        p = os.path.join(base(), entry["image"])
        return p if os.path.isfile(p) else None
    return None


def to_record(entry: dict) -> dict:
    """A card/worldbook-shaped record (title, kind, fields, summary, categories)."""
    return {"title": entry.get("title", ""), "kind": entry.get("kind", ""),
            "fields": entry.get("fields", {}), "summary": entry.get("body", ""),
            "categories": entry.get("categories", []), "url": f"codex:{entry.get('slug', '')}"}


def to_markdown(entry: dict) -> str:
    """An entry as Markdown, for the knowledge base to ingest and cite."""
    lines = [f"# {entry.get('title', '')}"]
    tier = CANON_TIERS.get(entry.get("canon", DEFAULT_TIER), "")
    sub = " · ".join(x for x in (entry.get("kind"), tier) if x)
    if sub:
        lines.append(f"*{sub}*")
    if entry.get("body"):
        lines += ["", entry["body"]]
    if entry.get("fields"):
        lines += [""] + [f"- **{k}:** {v}" for k, v in entry["fields"].items()]
    if entry.get("categories"):
        lines += ["", "Categories: " + ", ".join(entry["categories"])]
    if entry.get("source"):
        lines += ["", f"Source: {entry['source']}"]
    return "\n".join(lines)
