"""Read MediaWiki sites (Fandom/Wikia and any other MediaWiki) with the standard library only.

This is the shared machinery behind the lorebook and fanfiction plugins: it talks to a wiki's own
``api.php`` (search and page parse) and turns wikitext into plain text — the lead summary and the first
``{{Infobox …}}`` template's fields. Every network call is made through a ``fetch`` callable the caller
passes in (``libra.skills.fetcher().get``), so the SSRF guard and ``--allow-private`` still apply and the
functions here stay testable without a network.
"""
from __future__ import annotations

import json
import re
from urllib.parse import quote, unquote, urlencode, urlsplit

# Infobox fields that are layout/boilerplate rather than lore.
SKIP_KEYS = {"image", "image1", "imagewidth", "imagecaption", "imagebg", "caption", "logo", "cols",
             "title", "titlestyle", "headerstyle", "labelstyle", "datastyle", "float", "align", "width",
             "bodystyle", "row1", "pagename", "name"}
FILE_RE = re.compile(r"^(?:file:|image:|\[\[file:)|\.(?:png|jpe?g|gif|svg|webp)$", re.I)


# --------------------------------------------------------------- wikitext
def iter_templates(text: str):
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


def split_params(body: str) -> list:
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


def strip_wiki(v: str) -> str:
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


def remove_blocks(text: str) -> str:
    """Strip whole ``{{…}}`` templates and ``{| … |}`` tables so only prose is left."""
    while re.search(r"\{\{[^{}]*\}\}", text):
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    return re.sub(r"\{\|.*?\|\}", " ", text, flags=re.S)


def lead_summary(wikitext: str, limit: int = 600) -> str:
    """The opening prose of a wiki article, markup removed, up to a few sentences."""
    head = re.split(r"\n==", wikitext, maxsplit=1)[0]
    head = remove_blocks(head)
    head = re.sub(r"(?m)^\s*__[A-Z]+__\s*$", "", head)           # magic words like __NOTOC__
    text = strip_wiki(head).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    dot = cut.rfind(". ")
    return (cut[:dot + 1] if dot > limit // 2 else cut).strip() + " …"


def infobox_fields(wikitext: str) -> tuple:
    """(infobox name, {field: value}) from the first ``{{Infobox …}}`` template, or ("", {})."""
    for tmpl in iter_templates(wikitext):
        params = split_params(tmpl[2:-2])
        name = params[0].strip()
        if "infobox" not in name.lower():
            continue
        fields = {}
        for param in params[1:]:
            if "=" not in param:
                continue
            key, _, raw = param.partition("=")
            key = key.strip()
            if not key or key.lower() in SKIP_KEYS:
                continue
            val = strip_wiki(raw)
            if val and not FILE_RE.search(val) and len(val) < 400:
                fields[key] = val
        return name, fields
    return "", {}


# ------------------------------------------------------------- MediaWiki API
def wiki_targets(url: str):
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


def api_candidates(wiki: str):
    """Yield the ``api.php`` URLs to try for a wiki, from a domain or any page URL."""
    parts = urlsplit(wiki if re.match(r"https?://", wiki, re.I) else "https://" + wiki)
    if not parts.netloc:
        return
    base = f"{parts.scheme}://{parts.netloc}"
    prefixes = []
    m = re.match(r"(.*/)wiki/", parts.path)
    if m:
        prefixes.append(m.group(1))
    prefixes += ["/", "/w/"]
    seen = set()
    for p in prefixes:
        api = base + re.sub(r"//+", "/", p + "api.php")
        if api not in seen:
            seen.add(api)
            yield api


def api_parse(api: str, title: str, fetch) -> dict | None:
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


def api_search(api: str, query: str, limit: int, fetch) -> list | None:
    """Full-text search a MediaWiki, returning page titles. None if this isn't a MediaWiki search API."""
    q = urlencode({"action": "query", "list": "search", "srsearch": query, "srlimit": str(max(1, limit)),
                   "srnamespace": "0", "srprop": "", "format": "json", "formatversion": "2"})
    try:
        resp = fetch(api + "?" + q)
    except Exception:
        return None
    if resp.status != 200:
        return None
    try:
        data = json.loads(resp.text)
    except (ValueError, json.JSONDecodeError):
        return None
    search = data.get("query", {}).get("search") if isinstance(data.get("query"), dict) else None
    if search is None:
        return None
    return [row.get("title", "") for row in search if isinstance(row, dict) and row.get("title")]


def resolve_api(wiki: str, probe: str, fetch) -> str | None:
    """Find the working api.php for a wiki by trying a search against each candidate."""
    for api in api_candidates(wiki):
        if api_search(api, probe or "a", 1, fetch) is not None:
            return api
    return None


def article_url(api: str, title: str) -> str:
    parts = urlsplit(api)
    return f"{parts.scheme}://{parts.netloc}/wiki/" + quote(title.replace(" ", "_"), safe="/:()")


def page_summary(api: str, title: str, fetch, limit: int = 400) -> str:
    """A one-paragraph canon summary for a page title, or '' if it can't be read."""
    parse = api_parse(api, title, fetch)
    if parse is None:
        return ""
    wikitext = parse["wikitext"] if isinstance(parse["wikitext"], str) else parse["wikitext"].get("*", "")
    return lead_summary(wikitext, limit)
