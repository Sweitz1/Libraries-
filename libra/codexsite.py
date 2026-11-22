"""Serve the personal codex as a small local website — browse and add entries in a browser — plus a
MediaWiki-compatible ``api.php`` so Libra's own wiki tools (``worldbook``, ``worldbook_search``,
``world_build``) can read your codex exactly like a Fandom wiki.

It's plain ``http.server`` from the standard library, meant to run on ``127.0.0.1`` for one person. The
API turns each entry into the wikitext a MediaWiki ``action=parse`` would return — an ``{{Infobox …}}``
template built from the entry's fields, plus its body — so the existing reader parses it unchanged::

    libra skill run codex_serve                 # http://127.0.0.1:8800/
    libra --allow-private skill run worldbook http://127.0.0.1:8800/wiki/USS_Daedalus

(``--allow-private`` because the fetcher's SSRF guard blocks localhost by default.)
"""
from __future__ import annotations

import html
import json
import os
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from . import codex

_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{title}</title><style>
:root{{color-scheme:light dark;--bg:#f5f6f8;--panel:#fff;--ink:#1a1d24;--muted:#72777f;--line:#d8dbe0;
--accent:#2e4374;--accentink:#fff;--rowalt:#f3f5f8;--link:#1f5fbf}}
@media(prefers-color-scheme:dark){{:root{{--bg:#101318;--panel:#171b21;--ink:#e6e9ee;--muted:#9aa2ad;
--line:#2a2f37;--accent:#33508a;--rowalt:#1d222a;--link:#8ab4ff}}}}
*{{box-sizing:border-box}}
body{{font-family:'Segoe UI',system-ui,Helvetica,Arial,sans-serif;margin:0;background:var(--bg);color:var(--ink);
line-height:1.55}}
a{{color:var(--link);text-decoration:none}}a:hover{{text-decoration:underline}}
.site{{background:var(--accent);color:var(--accentink);padding:10px 20px;display:flex;align-items:center;gap:14px}}
.site a{{color:var(--accentink);font-weight:700}}.site .s{{margin-left:auto}}
.site input{{border:0;border-radius:16px;padding:6px 12px;width:min(260px,46vw)}}
.wrap{{max-width:960px;margin:0 auto;padding:22px 20px 60px}}
.article{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:22px 26px;overflow:hidden}}
.article:after{{content:"";display:block;clear:both}}
h1.page{{margin:0 0 2px;font-size:30px;font-weight:800}}
.tagline{{color:var(--muted);font-size:14px;border-bottom:2px solid var(--line);padding-bottom:12px;margin-bottom:16px}}
.lead p{{margin:0 0 12px}}
h2.sec{{font-size:21px;font-weight:700;border-bottom:1px solid var(--line);padding-bottom:4px;margin:26px 0 10px}}
.catbar{{clear:both;margin-top:26px;border-top:1px solid var(--line);padding-top:12px;color:var(--muted);font-size:13px}}
.catbar b{{color:var(--ink)}}
/* Fandom-style portable infobox */
.portable-infobox{{float:right;width:300px;margin:4px 0 18px 24px;background:var(--panel);border:1px solid var(--line);
border-radius:10px;overflow:hidden;font-size:14px;box-shadow:0 1px 3px rgba(0,0,0,.07)}}
.pi-title{{background:var(--accent);color:var(--accentink);font-weight:700;text-align:center;padding:11px 10px;font-size:17px}}
.pi-image img{{display:block;width:100%}}.pi-image-caption{{text-align:center;color:var(--muted);font-size:12px;padding:4px 8px}}
.portable-infobox h2,.portable-infobox h3{{margin:0;font-size:inherit;font-weight:inherit}}
.pi-data{{display:flex;align-items:stretch;border-top:1px solid var(--line)}}.pi-data:nth-child(odd){{background:var(--rowalt)}}
.pi-data-label{{flex:0 0 42%;font-weight:700;font-size:14px;padding:7px 10px}}
.pi-data-value{{flex:1;font-size:14px;padding:7px 10px;min-width:0;word-wrap:break-word}}
.pi-header{{background:var(--rowalt);font-weight:700;text-align:center;padding:5px;border-top:1px solid var(--line);
text-transform:uppercase;font-size:12px;letter-spacing:.04em;color:var(--muted)}}
/* index + form */
.box{{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px 20px;margin:18px 0}}
.entries{{list-style:none;padding:0;margin:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:10px}}
.entries a.card{{display:block;border:1px solid var(--line);border-radius:10px;padding:12px 14px;background:var(--panel)}}
.entries a.card:hover{{border-color:var(--accent);text-decoration:none}}
.entries .t{{font-weight:700;color:var(--ink)}}.entries .k{{color:var(--muted);font-size:13px}}
label{{font-weight:600;font-size:14px;display:block;margin-top:8px}}
input,textarea,select{{font:inherit;padding:9px;border:1px solid var(--line);border-radius:8px;width:100%;
margin:4px 0 10px;background:var(--bg);color:inherit}}
button{{font:inherit;background:var(--accent);color:var(--accentink);border:0;border-radius:8px;cursor:pointer;
padding:10px 20px;font-weight:600}}
.muted{{color:var(--muted)}}
.badge{{display:inline-block;font-size:12px;font-weight:700;padding:2px 9px;border-radius:12px;vertical-align:middle}}
.b-canon{{background:#1f5fbf;color:#fff}}.b-au{{background:#7a3fb0;color:#fff}}
.tiergroup{{margin:22px 0 6px;font-weight:800;font-size:15px;display:flex;align-items:center;gap:8px}}
.conts{{display:flex;flex-wrap:wrap;gap:8px;margin:10px 0 16px}}
.conts a{{padding:6px 14px;border:1px solid var(--line);border-radius:18px;background:var(--panel);font-weight:600}}
.conts a.on{{background:var(--accent);color:var(--accentink);border-color:var(--accent)}}
.links{{clear:both;margin-top:18px;font-size:14px}}.links b{{color:var(--ink)}}
@media(max-width:640px){{.portable-infobox{{float:none;width:auto;margin:0 0 16px}}.wrap{{padding:16px}}}}
</style></head><body><header class=site><a href="/">&#128214; Codex</a>
<form class=s method=get action="/"><input name=q placeholder="Search the codex…"></form></header>
<div class=wrap>{body}</div></body></html>"""


def build_wikitext(entry: dict) -> str:
    """Render an entry as the wikitext a MediaWiki ``action=parse`` would return (infobox + body)."""
    lines = ["{{Infobox " + (entry.get("kind") or "entry")]
    for key, value in entry.get("fields", {}).items():
        lines.append(f"| {key} = {value}")
    lines.append("}}")
    if entry.get("body"):
        lines += ["", entry["body"]]
    return "\n".join(lines)


def parse_payload(entry: dict) -> dict:
    """The ``parse`` object of a MediaWiki API response for an entry."""
    return {"title": entry.get("title", ""), "displaytitle": entry.get("title", ""),
            "wikitext": build_wikitext(entry),
            "categories": [{"category": c.replace(" ", "_")} for c in entry.get("categories", [])]}


def _parse_multipart(body: bytes, boundary: bytes):
    fields, files = {}, {}
    for part in body.split(b"--" + boundary):
        part = part.lstrip(b"\r\n")
        if not part or part.startswith(b"--") or b"\r\n\r\n" not in part:
            continue
        head, data = part.split(b"\r\n\r\n", 1)
        if data.endswith(b"\r\n"):
            data = data[:-2]
        headers = head.decode("latin-1")
        name = re.search(r'name="([^"]*)"', headers)
        if not name:
            continue
        filename = re.search(r'filename="([^"]*)"', headers)
        if filename and filename.group(1):
            files[name.group(1)] = (filename.group(1), data)
        else:
            fields[name.group(1)] = data.decode("utf-8", "replace")
    return fields, files


# ----------------------------------------------------------------- HTML views
def _infobox_html(entry: dict) -> str:
    """A Fandom portable-infobox: title bar, image, label/value rows (same classes Fandom uses)."""
    parts = [f'<aside class="portable-infobox"><h2 class="pi-title">{html.escape(entry.get("title", ""))}</h2>']
    if entry.get("image"):
        cap = html.escape(entry.get("kind") or "")
        parts.append(f'<figure class="pi-image"><img src="/{html.escape(entry["image"])}" alt="">'
                     + (f'<figcaption class="pi-image-caption">{cap}</figcaption>' if cap else "") + "</figure>")
    cont = codex.continuity(entry.get("continuity", codex.CANON_SLUG))
    overview = [("Type", html.escape(entry.get("kind") or codex.TYPE_LABELS.get(entry.get("type", "article"),
                                                                                "Article")[:-1]))]
    if cont:
        overview.append(("Continuity", html.escape(cont["name"])))
    if entry.get("date"):
        overview.append(("Date", html.escape(entry["date"])))
    if entry.get("source"):
        src = html.escape(entry["source"])
        val = f'<a href="{src}" rel="nofollow">{src}</a>' if re.match(r"https?://", entry["source"]) else src
        overview.append(("Source", val))
    parts.append('<div class="pi-header">Overview</div>')
    for label, value in overview:
        parts.append(f'<div class="pi-item pi-data"><h3 class="pi-data-label">{label}</h3>'
                     f'<div class="pi-data-value">{value}</div></div>')
    for key, value in entry.get("fields", {}).items():
        parts.append(f'<div class="pi-item pi-data"><h3 class="pi-data-label">{html.escape(str(key))}</h3>'
                     f'<div class="pi-data-value">{html.escape(str(value))}</div></div>')
    parts.append("</aside>")
    return "".join(parts)


def _inline(text: str) -> str:
    """Escape, then apply light wikitext: '''bold''', ''italic'', [[link]] / [[a|label]]."""
    t = html.escape(text, quote=False)
    t = re.sub(r"\[\[[^\]|]*\|([^\]]+)\]\]", r"\1", t)
    t = re.sub(r"\[\[([^\]]+)\]\]", r"\1", t)
    t = re.sub(r"'''(.+?)'''", r"<b>\1</b>", t)
    t = re.sub(r"''(.+?)''", r"<i>\1</i>", t)
    return t


def _body_html(body: str) -> str:
    """Render the description, turning '== Heading ==' or '## Heading' lines into section headers."""
    out, para = [], []

    def flush():
        if para:
            out.append("<p>" + _inline(" ".join(para)) + "</p>")
            para.clear()

    for line in body.splitlines():
        m = re.match(r"\s*(?:={2,}\s*(.+?)\s*={2,}|#{2,3}\s+(.+?))\s*$", line)
        if m:
            flush()
            out.append(f'<h2 class="sec">{_inline(m.group(1) or m.group(2))}</h2>')
        elif line.strip():
            para.append(line.strip())
        else:
            flush()
    flush()
    return "".join(out)


def _type_label(entry: dict) -> str:
    return entry.get("kind") or codex.TYPE_LABELS.get(entry.get("type", "article"), "Article")[:-1]


def _cont_badge(cont: dict) -> str:
    return f'<span class="badge b-{cont["type"]}">{html.escape(cont["name"])}</span>' if cont else ""


def _render_entry(entry: dict, href=None) -> str:
    href = href or (lambda c, s: f"/wiki/{c}/{s}")
    cont = codex.continuity(entry.get("continuity", codex.CANON_SLUG))
    infobox = _infobox_html(entry)
    lead = f'<div class="lead">{_body_html(entry["body"])}</div>' if entry.get("body") else ""
    tagline = f'<div class="tagline">{html.escape(_type_label(entry))} {_cont_badge(cont)}</div>'
    cats = ('<div class="catbar"><b>Categories:</b> '
            + " &middot; ".join(html.escape(c) for c in entry["categories"])
            + "</div>" if entry.get("categories") else "")
    cross = codex.links_of(entry)
    links = ('<div class="links"><b>Also in other continuities:</b> '
             + ", ".join(f'<a href="{href(c["slug"], e["slug"])}">{html.escape(e["title"])} '
                         f'({html.escape(c["name"])})</a>' for c, e in cross) + "</div>") if cross else ""
    empty = "" if (lead or entry.get("fields")) else '<p class="muted">This entry has no description yet.</p>'
    return (f'<article class="article"><h1 class="page">{html.escape(entry.get("title", ""))}</h1>{tagline}'
            f'{infobox}{lead}{empty}{links}{cats}</article>')


def _card_link(e: dict, href: str) -> str:
    cont = codex.continuity(e.get("continuity", codex.CANON_SLUG))
    return (f'<a class="card" href="{href}"><div class="t">{html.escape(e["title"])}</div>'
            f'<div class="k">{html.escape(_type_label(e))} {_cont_badge(cont)}</div></a>')


def _typed_listing(cont_slug: str, href) -> str:
    """A continuity's entries grouped by type (Characters, Locations, Factions, …)."""
    groups = codex.by_type(cont_slug)
    if not groups:
        return '<p class="muted">No entries in this continuity yet — add one below.</p>'
    out = []
    for etype, items in groups.items():
        out.append(f'<div class="tiergroup">{html.escape(codex.TYPE_LABELS.get(etype, etype))} '
                   f'<span class="muted">({len(items)})</span></div>')
        out.append('<ul class="entries">' + "".join(_card_link(e, href(e)) for e in items) + "</ul>")
    return "".join(out)


def _cont_tabs(active: str, href_for) -> str:
    tabs = []
    for c in codex.continuities():
        n = len(codex.all_entries(c["slug"]))
        on = " on" if c["slug"] == active else ""
        tabs.append(f'<a class="{on.strip()}" href="{href_for(c)}">{html.escape(c["name"])} '
                    f'<span class="muted">{n}</span></a>')
    return '<div class="conts">' + "".join(tabs) + "</div>"


def _add_form() -> str:
    conts = "".join(f'<option value="{c["slug"]}">{html.escape(c["name"])}</option>'
                    for c in codex.continuities())
    types = "".join(f'<option value="{t}">{html.escape(codex.TYPE_LABELS[t])}</option>'
                    for t in codex.ENTRY_TYPES)
    return ('<div class="box"><h2>Add / update an entry</h2>'
            '<form method=post action="/add" enctype="multipart/form-data">'
            '<label>Title</label><input name=title required>'
            f'<label>Continuity</label><select name=continuity>{conts}</select>'
            f'<label>Type</label><select name=type>{types}</select>'
            '<label>Kind (free label, optional)</label><input name=kind placeholder="e.g. ship, planet, villain">'
            '<label>Fields (one per line, key = value) — these become the infobox table</label>'
            '<textarea name=fields rows=5 placeholder="class = BC-304&#10;crew = 200"></textarea>'
            '<label>Description (use == Heading == for sections)</label><textarea name=body rows=5></textarea>'
            '<label>Date (optional — places it on the timeline)</label><input name=date placeholder="e.g. 2005">'
            '<label>Categories (comma-separated)</label><input name=categories>'
            '<label>Source (where this lore came from)</label><input name=source>'
            '<label>Image</label><input type=file name=image accept="image/*">'
            '<button type=submit>Save entry</button></form></div>')


def _render_index(active: str, query: str = "") -> str:
    if query:
        hits = codex.search(query, 100)
        listing = ('<ul class="entries">' + "".join(_card_link(e, f"/wiki/{e['continuity']}/{e['slug']}")
                                                     for e in hits) + "</ul>"
                   if hits else '<p class="muted">No matches.</p>')
        return f'<h1 class="page">Search: {html.escape(query)} <span class="muted">— {len(hits)}</span></h1>{listing}'
    cont = codex.continuity(active) or codex.continuities()[0]
    tabs = _cont_tabs(cont["slug"], lambda c: "/?c=" + c["slug"])
    desc = f'<p class="muted">{html.escape(cont["description"])}</p>' if cont.get("description") else ""
    listing = _typed_listing(cont["slug"], lambda e: f"/wiki/{e['continuity']}/{e['slug']}")
    return (f'<h1 class="page">{html.escape(cont["name"])} {_cont_badge(cont)}</h1>{desc}{tabs}'
            f'{listing}{_add_form()}')


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass                                                   # quiet

    def _send(self, body, status=200, ctype="text/html; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def _json(self, obj, status=200):
        self._send(json.dumps(obj), status, "application/json; charset=utf-8")

    def _page(self, inner, title="Codex"):
        self._send(_PAGE.format(title=html.escape(title), body=inner))

    def do_GET(self):
        parts = urlsplit(self.path)
        path, qs = parts.path, parse_qs(parts.query)
        if path == "/api.php":
            return self._api(qs)
        if path == "/":
            q = (qs.get("q") or [""])[0]
            active = codex.resolve_continuity((qs.get("c") or [""])[0])
            return self._page(_render_index(active, q))
        if path.startswith("/wiki/"):
            rest = [p for p in unquote(path[len("/wiki/"):]).split("/") if p]
            cont, slug = (rest[0], rest[1]) if len(rest) >= 2 else (codex.CANON_SLUG, rest[0] if rest else "")
            entry = codex.get(slug, cont)
            if entry is None:
                return self._page("<p>No such entry. <a href=/>Back</a></p>", "Not found")
            return self._page(_render_entry(entry), entry.get("title", "Entry"))
        if path.startswith("/images/"):
            rel = unquote(path[len("/images/"):])
            root = os.path.realpath(os.path.join(codex.base(), "images"))
            fpath = os.path.realpath(os.path.join(root, rel))
            if fpath.startswith(root + os.sep) and os.path.isfile(fpath):   # guard path traversal
                import mimetypes
                with open(fpath, "rb") as f:
                    return self._send(f.read(), 200, mimetypes.guess_type(fpath)[0] or "application/octet-stream")
            return self._send("not found", 404, "text/plain")
        return self._send("not found", 404, "text/plain")

    def _api(self, qs):
        action = (qs.get("action") or [""])[0]
        cont = codex.resolve_continuity((qs.get("continuity") or [""])[0])   # reads the canon by default
        if action == "parse":
            entry = codex.get((qs.get("page") or [""])[0], cont)
            if entry is None:
                return self._json({"error": {"code": "missingtitle", "info": "page not found"}})
            return self._json({"parse": parse_payload(entry)})
        if action == "query" and (qs.get("list") or [""])[0] == "search":
            limit = int((qs.get("srlimit") or ["10"])[0])
            hits = codex.search((qs.get("srsearch") or [""])[0], limit, continuity=cont)
            return self._json({"query": {"search": [{"title": e["title"]} for e in hits]}})
        return self._json({"error": {"code": "unknown_action"}})

    def do_HEAD(self):
        self.do_GET()

    def do_POST(self):
        if urlsplit(self.path).path != "/add":
            return self._send("not found", 404, "text/plain")
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        ctype = self.headers.get("Content-Type", "")
        fields, files = {}, {}
        if ctype.startswith("multipart/form-data"):
            m = re.search(r"boundary=(.*)", ctype)
            if m:
                fields, files = _parse_multipart(body, m.group(1).strip('"').encode("latin-1"))
        else:
            fields = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
        title = (fields.get("title") or "").strip()
        if not title:
            return self._send("<p>Title required. <a href=/>Back</a></p>", 400)
        field_map = {}
        for line in re.split(r"[\n;]", fields.get("fields", "")):
            key, sep, val = line.partition("=")
            if sep and key.strip():
                field_map[key.strip()] = val.strip()
        cats = [c.strip() for c in re.split(r"[,\n;]", fields.get("categories", "")) if c.strip()]
        image_path = None
        if files.get("image") and files["image"][1]:
            fname, data = files["image"]
            ext = os.path.splitext(fname)[1].lower()
            if ext in codex.IMAGE_EXT:
                tmp = os.path.join(codex.base(), "_upload" + ext)
                with open(tmp, "wb") as f:
                    f.write(data)
                image_path = tmp
        cont = codex.resolve_continuity(fields.get("continuity", ""))
        entry = codex.save(title, kind=fields.get("kind", "").strip(), type=fields.get("type", "").strip(),
                           fields=field_map, body=fields.get("body", "").strip(), categories=cats,
                           image=image_path, continuity=cont, date=fields.get("date", "").strip(),
                           source=fields.get("source", "").strip())
        if image_path and os.path.isfile(image_path):
            os.remove(image_path)
        self.send_response(303)                                # redirect to the new entry
        self.send_header("Location", f"/wiki/{entry['continuity']}/{entry['slug']}")
        self.end_headers()


def make_server(host: str = "127.0.0.1", port: int = 8800) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)


# -------------------------------------------------- static site generation
def _static_shell(title: str, inner: str, home: str = "index.html") -> str:
    """The page shell for the static build: home link given, no live search/forms."""
    page = _PAGE.replace('<a href="/">&#128214; Codex</a>\n'
                         '<form class=s method=get action="/"><input name=q placeholder="Search the codex…"></form>',
                         f'<a href="{home}">&#128214; Codex</a>')
    return page.format(title=html.escape(title), body=inner)


def build_site(out_dir: str) -> dict:
    """Generate the whole world as a static, text-only wiki site: an index grouped by continuity and type,
    a page per entry (``wiki/<continuity>/<slug>.html``), images, and working cross-world links.

    The lorebook is the single source of truth; this renders it, so the two never drift. Hostable on any
    static host — no server, no image model, no JavaScript."""
    import shutil

    conts = codex.continuities()
    entries = codex.all_entries()
    if os.path.isdir(os.path.join(codex.base(), "images")):     # copy images, keeping the continuity subdirs
        shutil.copytree(os.path.join(codex.base(), "images"), os.path.join(out_dir, "images"),
                        dirs_exist_ok=True)

    for entry in entries:                                        # one article page per entry, two levels deep
        page_dir = os.path.join(out_dir, "wiki", entry["continuity"])
        os.makedirs(page_dir, exist_ok=True)
        article = _render_entry(entry, href=lambda c, s: f"../{c}/{s}.html")
        article = article.replace('src="/images/', 'src="../../images/')
        doc = _static_shell(entry.get("title", "Entry"), article, home="../../index.html")
        with open(os.path.join(page_dir, entry["slug"] + ".html"), "w", encoding="utf-8") as f:
            f.write(doc)

    def href(e):
        return f"wiki/{e['continuity']}/{e['slug']}.html"

    sections = [f'<h1 class="page">Your world <span class="muted">&mdash; {len(entries)} '
                f'entr{"y" if len(entries) == 1 else "ies"} across {len(conts)} '
                f'continuit{"y" if len(conts) == 1 else "ies"}</span></h1>']
    for c in conts:
        n = len(codex.all_entries(c["slug"]))
        sections.append(f'<h2 class="sec">{html.escape(c["name"])} {_cont_badge(c)} '
                        f'<span class="muted">({n})</span></h2>')
        if c.get("description"):
            sections.append(f'<p class="muted">{html.escape(c["description"])}</p>')
        sections.append(_typed_listing(c["slug"], href))
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(_static_shell("Codex", "".join(sections)))
    return {"dir": out_dir, "entries": len(entries), "continuities": len(conts),
            "index": os.path.join(out_dir, "index.html")}
