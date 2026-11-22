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
from urllib.parse import quote, unquote, urlencode, urlsplit

from libra.skills import SkillResult, fetcher, skill

# Fields that are layout/boilerplate rather than lore, dropped from the card.
_SKIP_KEYS = {"image", "image1", "imagewidth", "imagecaption", "imagebg", "caption", "logo", "cols",
              "title", "titlestyle", "headerstyle", "labelstyle", "datastyle", "float", "align", "width",
              "bodystyle", "row1", "pagename", "name"}
_FILE_RE = re.compile(r"^(?:file:|image:|\[\[file:)|\.(?:png|jpe?g|gif|svg|webp)$", re.I)

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


# --------------------------------------------------------------- wikitext
def _iter_templates(text: str):
    """Yield each top-level ``{{…}}`` template (braces included), honouring nesting."""
    i, n = 0, len(text)
    while i < n:
        if text[i:i + 2] == "{{":
            depth, j = 1, i + 2
            while j < n and depth:
                two = text[j:j + 2]
                if two == "{{":
                    depth += 1
                    j += 2
                elif two == "}}":
                    depth -= 1
                    j += 2
                else:
                    j += 1
            yield text[i:j]
            i = j
        else:
            i += 1


def _split_params(body: str) -> list:
    """Split a template body on top-level ``|``, ignoring ``|`` inside nested templates, links and tables."""
    parts, depth, i, n, cur = [], 0, 0, len(body), []
    while i < n:
        two = body[i:i + 2]
        if two in ("{{", "[[", "{|"):
            depth += 1
            cur.append(two)
            i += 2
            continue
        if two in ("}}", "]]", "|}"):
            depth = max(0, depth - 1)
            cur.append(two)
            i += 2
            continue
        ch = body[i]
        if ch == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
        i += 1
    parts.append("".join(cur))
    return parts


def _strip_wiki(v: str) -> str:
    """Turn a wikitext value into plain text."""
    v = re.sub(r"<ref[^>]*>.*?</ref>", "", v, flags=re.S | re.I)
    v = re.sub(r"<ref[^>]*/>", "", v, flags=re.I)
    v = re.sub(r"<br\s*/?>", ", ", v, flags=re.I)
    v = re.sub(r"<!--.*?-->", "", v, flags=re.S)
    v = re.sub(r"<[^>]+>", "", v)                                 # any other HTML tag
    while re.search(r"\{\{[^{}]*\}\}", v):                        # drop templates, innermost first
        v = re.sub(r"\{\{[^{}]*\}\}", " ", v)
    v = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", v)            # [[target|label]] -> label
    v = re.sub(r"\[\[([^\]]*)\]\]", r"\1", v)                     # [[target]] -> target
    v = re.sub(r"\[https?://\S+\s+([^\]]*)\]", r"\1", v)          # [url label] -> label
    v = re.sub(r"\[https?://\S+\]", "", v)
    v = v.replace("'''", "").replace("''", "")                   # bold / italic
    v = re.sub(r"(?m)^\s*[\*#:;]+\s*", "", v)                     # list bullets
    v = re.sub(r"\s*\n\s*", ", ", v.strip())                     # multi-line value -> comma list
    return re.sub(r"\s+", " ", v).strip(" ,;")


def _infobox_fields(wikitext: str) -> tuple:
    """(infobox name, {field: value}) from the first ``{{Infobox …}}`` template, or ("", {})."""
    for tmpl in _iter_templates(wikitext):
        params = _split_params(tmpl[2:-2])
        name = params[0].strip()
        if "infobox" not in name.lower():
            continue
        fields = {}
        for param in params[1:]:
            if "=" not in param:
                continue
            key, _, raw = param.partition("=")
            key = key.strip()
            if not key or key.lower() in _SKIP_KEYS:
                continue
            val = _strip_wiki(raw)
            if val and not _FILE_RE.search(val) and len(val) < 400:
                fields[key] = val
        return name, fields
    return "", {}


def _remove_blocks(text: str) -> str:
    """Strip whole ``{{…}}`` templates and ``{| … |}`` tables so only prose is left."""
    while re.search(r"\{\{[^{}]*\}\}", text):
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    text = re.sub(r"\{\|.*?\|\}", " ", text, flags=re.S)
    return text


def _lead_summary(wikitext: str, limit: int = 600) -> str:
    """The opening prose of a wiki article, markup removed, up to a few sentences."""
    head = re.split(r"\n==", wikitext, maxsplit=1)[0]
    head = _remove_blocks(head)
    head = re.sub(r"(?m)^\s*__[A-Z]+__\s*$", "", head)           # magic words like __NOTOC__
    text = _strip_wiki(head).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = cut.rfind(". ")
    return (cut[:dot + 1] if dot > limit // 2 else cut).strip() + " …"


# ------------------------------------------------------------- MediaWiki API
def _wiki_targets(url: str):
    """For a ``…/wiki/<Title>`` URL, yield (api_url, page_title) candidates to try."""
    parts = urlsplit(url)
    if not parts.scheme.startswith("http"):
        return
    m = re.match(r"(.*/)wiki/([^?#]+)", parts.path)
    if not m:
        return
    title = unquote(m.group(2)).replace("_", " ")
    base = f"{parts.scheme}://{parts.netloc}"
    prefix = m.group(1)                                          # e.g. "/" (Fandom) or "/w/" (some wikis)
    seen = set()
    for api_path in (prefix + "api.php", "/w/api.php", "/api.php"):
        api = base + re.sub(r"//+", "/", api_path)
        if api not in seen:
            seen.add(api)
            yield api, title


def _api_parse(api: str, title: str, fetch) -> dict | None:
    """Ask a MediaWiki API for a page's wikitext and categories. None if it isn't a MediaWiki API."""
    query = urlencode({"action": "parse", "page": title, "redirects": "1",
                       "prop": "wikitext|categories|displaytitle", "format": "json", "formatversion": "2"})
    try:
        resp = fetch(api + "?" + query)
    except Exception:
        return None
    if resp.status != 200 or "json" not in resp.content_type and not resp.text.lstrip().startswith("{"):
        return None
    try:
        data = json.loads(resp.text)
    except (ValueError, json.JSONDecodeError):
        return None
    parse = data.get("parse")
    if not isinstance(parse, dict) or "wikitext" not in parse:
        return None
    return parse


def _wiki_entry(url: str, fetch) -> dict | None:
    for api, title in _wiki_targets(url):
        parse = _api_parse(api, title, fetch)
        if parse is None:
            continue
        wikitext = parse["wikitext"] if isinstance(parse["wikitext"], str) else parse["wikitext"].get("*", "")
        name, fields = _infobox_fields(wikitext)
        cats = [c.get("category", "") if isinstance(c, dict) else str(c) for c in parse.get("categories", [])]
        cats = [c.replace("_", " ") for c in cats if c and not c.lower().startswith(("hidden", "stub"))]
        disp = parse.get("displaytitle") or parse.get("title") or title
        disp = re.sub(r"<[^>]+>", "", str(disp))
        return {"title": disp, "url": url, "kind": _guess_kind(name, cats),
                "summary": _lead_summary(wikitext), "fields": fields, "categories": cats,
                "source": "mediawiki-api", "infobox": name}
    return None


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


def _route(message: str):
    m = re.search(r"\b(?:lore|lorebook|worldbook|world ?book|infobox|wiki|character|spec(?:s|\s*sheet)?)\b"
                  r".{0,40}?(https?://\S+)", message, re.I)
    return {"url": m.group(1)} if m else None


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
