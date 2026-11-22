"""Libra plugin: build a structured *lorebook card* from a fan wiki or fan site.

Point it at a character, ship, species, place or technology page on a fan wiki (Fandom/Wikia and
most MediaWiki sites) or another fan site, and it pulls out the *structured* facts — the title, a short
summary, the infobox fields (the "spec data": affiliation, class, armament, first appearance …) and the
page's categories — instead of dumping the whole article as a wall of text ("a lore drop").

    libra skill run worldbook https://stargate.fandom.com/wiki/Daedalus
    libra skill run worldbook https://memory-alpha.fandom.com/wiki/USS_Enterprise_(NCC-1701) --out ship.json

How it works, with no third-party library:
  - Fandom / MediaWiki pages (``…/wiki/<Title>``): it asks the site's own MediaWiki API for the page
    wikitext and categories, then reads the first ``{{Infobox …}}`` template into clean key → value pairs
    and takes the lead paragraph as the summary. This is exact structured data, straight from the source.
  - Any other page: it fetches the HTML and looks for an infobox (Fandom ``aside.portable-infobox`` or a
    classic ``table.infobox``) or a definition list, plus the opening paragraphs.

All fetching goes through Libra's guarded fetcher (same SSRF checks as the rest of Libra), and the result
is marked as outside content. Load with ``--plugins plugins/`` and accept once:
``libra trust accept-plugin plugins/lorebook.py``.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlsplit

from libra import wiki as _wiki
from libra.skills import SkillResult, fetcher, skill

# The MediaWiki transport and wikitext parsing live in libra.wiki; these short names are what the code
# below (and the tests) use.
_SKIP_KEYS, _FILE_RE = _wiki.SKIP_KEYS, _wiki.FILE_RE
_iter_templates, _split_params, _strip_wiki = _wiki.iter_templates, _wiki.split_params, _wiki.strip_wiki
_infobox_fields, _remove_blocks, _lead_summary = _wiki.infobox_fields, _wiki.remove_blocks, _wiki.lead_summary
_wiki_targets, _api_parse, _api_candidates = _wiki.wiki_targets, _wiki.api_parse, _wiki.api_candidates
_api_search, _article_url, _resolve_api = _wiki.api_search, _wiki.article_url, _wiki.resolve_api

# What kind of thing the page is, guessed from the infobox name or categories.
_KIND_HINTS = [
    ("character", ("character", "person", "individual", "people", "villain", "hero", "crew")),
    ("species", ("species", "race", "creature", "alien", "lifeform", "animal")),
    ("ship", ("ship", "starship", "vessel", "spacecraft", "vehicle", "cruiser", "fighter")),
    ("technology", ("technolog", "device", "weapon", "equipment", "artifact", "item", "gadget")),
    ("location", ("location", "place", "planet", "world", "city", "building", "region", "country")),
    ("organization", ("organization", "organisation", "faction", "group", "government", "military")),
    ("event", ("event", "battle", "war", "mission", "incident")),
    ("episode", ("episode", "chapter", "issue", "story", "book", "film", "movie")),
]


def _guess_kind(name: str, categories: list) -> str:
    hay = (name + " " + " ".join(categories)).lower()
    for kind, words in _KIND_HINTS:
        if any(w in hay for w in words):
            return kind
    return "entry"


def _card_from_parse(parse: dict, url: str) -> dict:
    """Build a lorebook card from a MediaWiki ``action=parse`` result."""
    wikitext = parse["wikitext"] if isinstance(parse["wikitext"], str) else parse["wikitext"].get("*", "")
    name, fields = _infobox_fields(wikitext)
    cats = [c.get("category", "") if isinstance(c, dict) else str(c) for c in parse.get("categories", [])]
    cats = [c.replace("_", " ") for c in cats if c and not c.lower().startswith(("hidden", "stub"))]
    disp = parse.get("displaytitle") or parse.get("title") or url.rsplit("/wiki/", 1)[-1].replace("_", " ")
    disp = re.sub(r"<[^>]+>", "", str(disp))
    return {"title": disp, "url": url, "kind": _guess_kind(name, cats),
            "summary": _lead_summary(wikitext), "fields": fields, "categories": cats,
            "source": "mediawiki-api", "infobox": name}


def _wiki_entry(url: str, fetch) -> dict | None:
    for api, title in _wiki_targets(url):
        parse = _api_parse(api, title, fetch)
        if parse is not None:
            return _card_from_parse(parse, url)
    return None


# ----------------------------------------------------- search & world build
def _pull_titles(api: str, titles: list, fetch, seen: set, limit: int) -> list:
    """Fetch and card-ify each title (skipping ones with no infobox and no summary, and duplicates)."""
    cards = []
    for title in titles:
        if len(cards) >= limit or title in seen:
            continue
        seen.add(title)
        parse = _api_parse(api, title, fetch)
        if parse is None:
            continue
        card = _card_from_parse(parse, _article_url(api, title))
        if card["fields"] or card["summary"]:
            cards.append(card)
    return cards


def _parse_seeds(seeds: str) -> list:
    """``wiki = term | term ; wiki2 = term`` -> [(wiki, [terms]), …]."""
    out = []
    for part in seeds.split(";"):
        wiki, sep, terms = part.strip().partition("=")
        wiki = wiki.strip()
        term_list = [t.strip() for t in terms.split("|") if t.strip()] if sep else []
        if wiki and term_list:
            out.append((wiki, term_list))
    return out


# ------------------------------------------------------------------ HTML
def _pairs_from_box(box) -> dict:
    fields = {}
    data_items = box.select(".pi-data")                         # Fandom portable infobox
    if data_items:
        for item in data_items:
            label = item.select_one(".pi-data-label")
            value = item.select_one(".pi-data-value")
            if label and value and label.text and value.text:
                fields[label.text.strip(": ")] = value.text
        if fields:
            return fields
    for tr in box.find_all("tr"):                              # classic infobox table rows
        cells = [c for c in tr.children if getattr(c, "tag", None) in ("th", "td")]
        if len(cells) == 2 and cells[0].tag == "th" and cells[1].text:
            fields[cells[0].text.strip(": ")] = cells[1].text
    return fields


def _html_entry(url: str, markup: str) -> dict:
    from libra.web import _main_node, parse_html

    doc = parse_html(markup)
    box = (doc.select_one("aside.portable-infobox") or doc.select_one("table.infobox")
           or doc.select_one(".infobox") or doc.select_one(".infoboxtable"))
    fields = _pairs_from_box(box) if box is not None else {}
    if not fields:                                            # fall back to a definition list
        for dl in doc.find_all("dl"):
            kids = [c for c in dl.children if getattr(c, "tag", None) in ("dt", "dd")]
            i = 0
            while i + 1 < len(kids):
                if kids[i].tag == "dt" and kids[i + 1].tag == "dd" and kids[i].text and kids[i + 1].text:
                    fields[kids[i].text.strip(": ")] = kids[i + 1].text
                i += 1
            if fields:
                break
    fields = {k: v for k, v in fields.items() if k.lower() not in _SKIP_KEYS and not _FILE_RE.search(v)}
    title_box = box.select_one(".pi-title") if box is not None else None
    h1 = doc.find("h1")
    title_node = doc.find("title")
    title = (title_box.text if title_box else "") or (h1.text if h1 else "") or (title_node.text if title_node else "")
    paras = [p.text for p in _main_node(doc).find_all("p") if len(p.text) > 40]
    summary = " ".join(paras[:2])[:600].strip()
    cats = [a.text for a in doc.select(".page-header__categories a, .category a, #catlinks a") if a.text]
    return {"title": title.strip(), "url": url, "kind": _guess_kind(title, cats), "summary": summary,
            "fields": fields, "categories": cats, "source": "html", "infobox": ""}


# --------------------------------------------------------------- rendering
def _render(entry: dict) -> str:
    out = [f"# {entry['title']}" + (f"  ({entry['kind']})" if entry["kind"] != "entry" else "")]
    if entry["summary"]:
        out += ["", entry["summary"]]
    if entry["fields"]:
        out += ["", "## Spec data"]
        out += [f"- **{k}:** {v}" for k, v in entry["fields"].items()]
    elif not entry["summary"]:
        out += ["", "(No infobox or summary found on this page.)"]
    if entry["categories"]:
        out += ["", "Categories: " + ", ".join(entry["categories"][:12])]
    out += ["", f"Source: {entry['url']}  [{entry['source']}]"]
    return "\n".join(out)


def _render_world(record: dict, out: str) -> str:
    by_wiki: dict = {}
    for e in record["world"]:
        by_wiki.setdefault(e.get("source_wiki", record.get("sources", [""])[0] if record.get("sources") else ""),
                           []).append(e)
    lines = [f"# {record.get('title', 'World')}", f"{record['count']} entries "
             f"from {len(by_wiki)} source(s)", ""]
    for wiki, entries in by_wiki.items():
        lines.append(f"## {wiki}  ({len(entries)})")
        for e in entries:
            kind = f" [{e['kind']}]" if e["kind"] != "entry" else ""
            nfields = len(e["fields"])
            lines.append(f"- **{e['title']}**{kind} — {nfields} spec field(s)"
                         + (f"; {e['summary'][:100]}…" if e["summary"] else ""))
        lines.append("")
    if record.get("problems"):
        lines += ["Problems:"] + [f"- {p}" for p in record["problems"]] + [""]
    lines.append("Sources: " + ", ".join(record.get("sources", [])))
    if out:
        lines += ["", f"[world written to {out} — add it with: libra kb add {out}]"]
    return "\n".join(lines)


def _route(message: str):
    m = re.search(r"\b(?:lore|lorebook|worldbook|world ?book|infobox|wiki|character|spec(?:s|\s*sheet)?)\b"
                  r".{0,40}?(https?://\S+)", message, re.I)
    return {"url": m.group(1)} if m else None


@skill("worldbook_search", "Search a fan wiki and pull the matching pages as lorebook cards, so you don't "
       "have to find each page URL yourself.",
       {"wiki": "the wiki, e.g. https://stargate.fandom.com", "query": "what to search for, e.g. Asgard",
        "limit": "most pages to pull (default 5)", "out": "optional .json file to write the cards to"},
       risk="read", external=True)
def worldbook_search(wiki: str, query: str, limit: int = 5, out: str = "") -> SkillResult:
    limit = max(1, min(int(limit), 25))
    fetch = fetcher(delay=0.3).get
    api = _resolve_api(wiki, query, fetch)
    if api is None:
        return SkillResult("worldbook_search", False,
                           f"Couldn't reach a MediaWiki search API for {wiki}. Give the wiki's address, "
                           "e.g. https://stargate.fandom.com.")
    titles = _api_search(api, query, limit, fetch) or []
    entries = _pull_titles(api, titles, fetch, set(), limit)
    if not entries:
        return SkillResult("worldbook_search", False,
                           f"No pages with lore matched “{query}” on {wiki}.")
    root = "{0.scheme}://{0.netloc}".format(urlsplit(api))
    record = {"title": f"{query} — {root}", "sources": [root], "seeds": f"{wiki}={query}",
              "count": len(entries), "world": [dict(e, source_wiki=root) for e in entries]}
    if out:
        import os

        with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return SkillResult("worldbook_search", True, _render_world(record, out), record)


@skill("world_build", "Build a combined world file by searching one or more fan wikis for the lore you name "
       "and pulling the pages automatically — no manual page URLs.",
       {"seeds": "wiki = term | term ; wiki2 = term  (e.g. "
                 "https://stargate.fandom.com=Asgard|Daedalus ; https://masseffect.fandom.com=Reapers|Normandy)",
        "limit": "most pages per search term (default 5)", "name": "a name for the world",
        "out": "optional .json file to write the world to"},
       risk="read", external=True)
def world_build(seeds: str, limit: int = 5, name: str = "Test world", out: str = "") -> SkillResult:
    pairs = _parse_seeds(seeds)
    if not pairs:
        raise ValueError("world_build needs seeds like  "
                         "https://stargate.fandom.com=Asgard|Daedalus ; https://masseffect.fandom.com=Reapers")
    limit = max(1, min(int(limit), 25))
    fetch = fetcher(delay=0.3).get
    world, sources, problems, total_cap = [], [], [], 80
    for wiki, terms in pairs:
        api = _resolve_api(wiki, terms[0], fetch)
        if api is None:
            problems.append(f"no MediaWiki API found for {wiki}")
            continue
        root = "{0.scheme}://{0.netloc}".format(urlsplit(api))
        sources.append(root)
        seen: set = set()
        before = len(world)
        for term in terms:
            if len(world) >= total_cap:
                break
            titles = _api_search(api, term, limit, fetch) or []
            for card in _pull_titles(api, titles, fetch, seen, limit):
                card["source_wiki"] = root
                world.append(card)
        if len(world) == before:
            problems.append(f"{root}: no lore pages matched {', '.join(terms)}")
    if not world:
        return SkillResult("world_build", False,
                           "No lore pulled. " + (" ".join(problems) or "Check the wiki URLs and search terms."))
    record = {"title": name, "seeds": seeds, "sources": sources, "count": len(world), "world": world}
    if problems:
        record["problems"] = problems
    if out:
        import os

        with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return SkillResult("world_build", True, _render_world(record, out), record)


@skill("worldbook", "Pull a structured lorebook card (summary + infobox spec fields) from a fan wiki or fan "
       "site, instead of dumping the whole article.",
       {"url": "the character/ship/world/etc. page to read", "out": "optional .json file to write the card to"},
       router=_route, risk="read", external=True)
def worldbook(url: str, out: str = "") -> SkillResult:
    if not re.match(r"https?://", url, re.I):
        raise ValueError("worldbook needs a http(s) URL to a fan-wiki or fan-site page")
    fetch = fetcher().get
    entry = _wiki_entry(url, fetch)
    if entry is None:                                         # not a MediaWiki page: scrape the HTML
        try:
            resp = fetch(url)
        except Exception as exc:
            return SkillResult("worldbook", False, f"Couldn't fetch {url}: {exc}")
        if resp.status != 200:
            return SkillResult("worldbook", False, f"{url} returned HTTP {resp.status}.")
        if not resp.is_html:
            return SkillResult("worldbook", False, f"{url} isn't an HTML page ({resp.content_type}).")
        entry = _html_entry(url, resp.text)
    if not entry["fields"] and not entry["summary"]:
        return SkillResult("worldbook", False,
                           f"No lore found at {url} — no infobox, summary or definition list on the page. "
                           "Try the specific character/ship/world page rather than a hub or list page.")
    if out:
        import os

        out = os.path.expanduser(out)
        with open(out, "w", encoding="utf-8") as f:
            json.dump(entry, f, ensure_ascii=False, indent=2)
    text = _render(entry)
    if out:
        text += f"\n\n[card written to {out}]"
    return SkillResult("worldbook", True, text, entry)
