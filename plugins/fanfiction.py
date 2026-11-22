"""Libra plugins for fanfiction.net: the semi-structured bits it *does* expose.

Fanfiction.net has no infobox and no API (unlike a Fandom/MediaWiki wiki — see ``plugins/lorebook.py``),
but a story page and a community page both carry a machine-readable *metadata line* — rating, language,
genre, characters, chapters, words, reviews, favs, follows, status, dates — and the fandom breadcrumb.
These two skills read exactly that, rather than the story prose:

  - ``fic_meta``       one story page (``/s/<id>/…``) -> its fandom(s) and metadata fields.
  - ``fic_community``  a community page (``/community/<name>/<id>/``) -> the list of collected stories,
                       each with its own title, author, summary and metadata.

    libra skill run fic_meta https://www.fanfiction.net/s/11111/1/
    libra skill run fic_community https://www.fanfiction.net/community/Crossovers/1076/ --out list.json

Note: this reads *catalogue metadata*, not world lore or specs — fanfiction.net doesn't publish those.
For characters/ships/world specs, use ``worldbook`` on the fandom's wiki. Fanfiction.net also sits behind
a bot filter, so a live fetch may return a challenge page instead of HTML; the skill reports that plainly.
All fetching goes through Libra's guarded fetcher. Accept once: ``libra trust accept-plugin
plugins/fanfiction.py``.
"""
from __future__ import annotations

import json
import os
import re

from libra.skills import SkillResult, fetcher, skill

# Bare tokens in the metadata line are classified by these; anything left over is the character list.
_LANGUAGES = {"english", "spanish", "french", "german", "portuguese", "italian", "dutch", "polish",
              "russian", "chinese", "japanese", "korean", "indonesian", "filipino", "hungarian",
              "swedish", "norwegian", "danish", "finnish", "greek", "turkish", "czech", "romanian",
              "catalan", "ukrainian", "hebrew", "arabic", "vietnamese", "thai", "hindi", "esperanto"}
_GENRES = {"adventure", "angst", "romance", "humor", "drama", "poetry", "parody", "mystery", "fantasy",
           "horror", "supernatural", "suspense", "sci-fi", "friendship", "crime", "family", "hurt",
           "comfort", "tragedy", "western", "spiritual"}
# key: value pairs that may appear in the line
_FIELD_KEYS = {"chapters", "words", "reviews", "favs", "follows", "updated", "published", "status", "id"}


def _fetch(url: str):
    if not re.match(r"https?://", url, re.I):
        raise ValueError("a http(s) fanfiction.net URL is required")
    resp = fetcher().get(url)
    return resp


def _looks_blocked(resp) -> bool:
    low = resp.text[:2000].lower()
    return ("just a moment" in low or "cf-browser-verification" in low or "enable javascript and cookies"
            in low or "attention required" in low)


def _classify_bare(token: str, meta: dict):
    low = token.lower()
    if low in _LANGUAGES and "language" not in meta:
        meta["language"] = token
        return
    parts = [p.strip().lower() for p in low.split("/") if p.strip()]
    if parts and all(p in _GENRES for p in parts) and "genre" not in meta:
        meta["genre"] = token
        return
    meta.setdefault("characters", token)          # the one remaining bare token is the character list


def _parse_meta_line(text: str) -> dict:
    """Parse a fanfiction.net metadata line into fields. Tolerant of missing/extra parts."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^crossover\s*-\s*", "", text, flags=re.I)   # community rows prefix this
    meta: dict = {}
    for raw in text.split(" - "):
        token = raw.strip()
        if not token:
            continue
        key, sep, val = token.partition(":")
        key_l = key.strip().lower()
        if key_l == "rated":
            meta["rating"] = re.sub(r"^fiction\s+", "", val.strip(), flags=re.I)
        elif sep and key_l in _FIELD_KEYS:
            meta[key_l] = val.strip()
        else:
            _classify_bare(token, meta)
    return meta


def _first_user_link(node):
    for a in node.select("a[href^=/u/]"):
        if a.text:
            return a.text
    return ""


def _meta_node_text(scope):
    """The text of the span holding the 'Rated: … - Words: …' line, if present."""
    for n in scope.iter():
        if n.tag in ("span", "div") and "Words:" in n.text and " - " in n.text:
            return n.text
    return ""


# --------------------------------------------------------------- story page
def _route_meta(message: str):
    m = re.search(r"(https?://(?:www\.)?fanfiction\.net/s/\d+\S*)", message, re.I)
    return {"url": m.group(1)} if m else None


@skill("fic_meta", "Read a fanfiction.net story's fandom and metadata (rating, genre, characters, words, "
       "status …) — the catalogue data, not the story text.",
       {"url": "the story URL, e.g. https://www.fanfiction.net/s/11111/1/",
        "out": "optional .json file to write the record to"},
       router=_route_meta, risk="read", external=True)
def fic_meta(url: str, out: str = "") -> SkillResult:
    from libra.web import parse_html

    resp = _fetch(url)
    if resp.status != 200:
        return SkillResult("fic_meta", False, f"{url} returned HTTP {resp.status}.")
    if _looks_blocked(resp):
        return SkillResult("fic_meta", False,
                           "fanfiction.net returned its bot-check page instead of the story (it blocks "
                           "automated fetches). Open the page in a browser, save the HTML, and read that.")
    doc = parse_html(resp.text)
    top = doc.select_one("#profile_top") or doc
    title_node = top.select_one("b.xcontrast_txt") or top.select_one("b") or doc.find("title")
    author = _first_user_link(top)
    summary_node = top.select_one("div.xcontrast_txt")
    fandoms = [a.text for a in doc.select("#pre_story_links a") if a.text]
    meta = _parse_meta_line(_meta_node_text(top))
    record = {"url": url, "title": title_node.text if title_node else "", "author": author,
              "summary": summary_node.text if summary_node else "",
              "fandoms": fandoms, "crossover": len(fandoms) > 1 or any("Crossover" in f for f in fandoms),
              **meta}
    if not record["title"] and not meta:
        return SkillResult("fic_meta", False,
                           f"Couldn't find a story on {url}. Make sure it's a story page (…/s/<id>/…).")
    if out:
        with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return SkillResult("fic_meta", True, _render_story(record, out), record)


def _render_story(r: dict, out: str) -> str:
    lines = [f"# {r['title']}" + (f" — by {r['author']}" if r["author"] else "")]
    if r["fandoms"]:
        lines.append(("Crossover: " if r["crossover"] else "Fandom: ") + " × ".join(r["fandoms"]))
    if r["summary"]:
        lines += ["", r["summary"]]
    facts = [(k, r[k]) for k in ("rating", "language", "genre", "characters", "chapters", "words",
                                 "reviews", "favs", "follows", "status", "updated", "published") if r.get(k)]
    if facts:
        lines += ["", "## Metadata"] + [f"- **{k}:** {v}" for k, v in facts]
    lines += ["", f"Source: {r['url']}  [fanfiction.net]"]
    if out:
        lines += ["", f"[record written to {out}]"]
    return "\n".join(lines)


# ----------------------------------------------------------- community page
def _story_row(row) -> dict:
    title_node = row.select_one("a.stitle")
    meta_node = row.select_one(".z-padtop2") or row.select_one(".xgray")
    indent = row.select_one(".z-indent")
    summary = indent.text if indent else ""
    meta_text = meta_node.text if meta_node else ""
    if meta_text and summary.endswith(meta_text):                 # strip the meta out of the summary blob
        summary = summary[: -len(meta_text)].strip()
    return {"title": title_node.text if title_node else "", "author": _first_user_link(row),
            "summary": summary, **_parse_meta_line(meta_text)}


def _route_community(message: str):
    m = re.search(r"(https?://(?:www\.)?fanfiction\.net/community/\S+)", message, re.I)
    return {"url": m.group(1)} if m else None


@skill("fic_community", "List the stories collected in a fanfiction.net community, each with its author, "
       "summary and metadata.",
       {"url": "the community URL, e.g. https://www.fanfiction.net/community/Crossovers/1076/",
        "limit": "most stories to return (default 50)", "out": "optional .json file to write the list to"},
       router=_route_community, risk="read", external=True)
def fic_community(url: str, limit: int = 50, out: str = "") -> SkillResult:
    from libra.web import parse_html

    resp = _fetch(url)
    if resp.status != 200:
        return SkillResult("fic_community", False, f"{url} returned HTTP {resp.status}.")
    if _looks_blocked(resp):
        return SkillResult("fic_community", False,
                           "fanfiction.net returned its bot-check page instead of the community (it blocks "
                           "automated fetches). Open the page in a browser, save the HTML, and read that.")
    doc = parse_html(resp.text)
    name_node = doc.select_one("#content_wrapper_inner span.xcontrast_txt") or doc.find("title")
    rows = [r for r in (_story_row(row) for row in doc.select(".z-list")) if r["title"]]
    rows = rows[: max(0, int(limit))]
    if not rows:
        return SkillResult("fic_community", False,
                           f"No stories found on {url}. Make sure it's a community page "
                           "(…/community/<name>/<id>/) with a story list.")
    record = {"url": url, "community": name_node.text if name_node else "", "count": len(rows), "stories": rows}
    if out:
        with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return SkillResult("fic_community", True, _render_community(record, out), record)


def _render_community(r: dict, out: str) -> str:
    lines = [f"# {r['community']}" if r["community"] else "# Community", f"{r['count']} stories", ""]
    for i, s in enumerate(r["stories"], 1):
        tags = " · ".join(str(s[k]) for k in ("rating", "genre", "words", "status") if s.get(k))
        head = f"{i}. **{s['title']}**" + (f" — {s['author']}" if s["author"] else "")
        lines.append(head + (f"  ({tags})" if tags else ""))
        if s["summary"]:
            lines.append(f"   {s['summary'][:200]}")
    lines += ["", f"Source: {r['url']}  [fanfiction.net]"]
    if out:
        lines += ["", f"[list written to {out}]"]
    return "\n".join(lines)
