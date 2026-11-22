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
    if entry.get("kind"):
        parts.append('<div class="pi-header">Overview</div>'
                     f'<div class="pi-item pi-data"><h3 class="pi-data-label">Type</h3>'
                     f'<div class="pi-data-value">{html.escape(entry["kind"])}</div></div>')
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


def _render_entry(entry: dict) -> str:
    infobox = _infobox_html(entry) if (entry.get("fields") or entry.get("image") or entry.get("kind")) else ""
    lead = f'<div class="lead">{_body_html(entry["body"])}</div>' if entry.get("body") else ""
    tagline = f'<div class="tagline">{html.escape(entry.get("kind") or "entry")}</div>'
    cats = ('<div class="catbar"><b>Categories:</b> '
            + " &middot; ".join(f'<a href="/?q={html.escape(c)}">{html.escape(c)}</a>' for c in entry["categories"])
            + "</div>" if entry.get("categories") else "")
    empty = "" if (lead or infobox) else '<p class="muted">This entry has no description or fields yet.</p>'
    return (f'<article class="article"><h1 class="page">{html.escape(entry.get("title", ""))}</h1>{tagline}'
            f'{infobox}{lead}{empty}{cats}</article>')


def _render_index(entries: list, query: str = "") -> str:
    cards = "".join(f'<a class="card" href="/wiki/{html.escape(e["slug"])}"><div class="t">{html.escape(e["title"])}'
                    f'</div><div class="k">{html.escape(e.get("kind") or "entry")}</div></a>' for e in entries)
    listing = (f'<ul class="entries">{cards}</ul>' if cards
               else '<p class="muted">No entries yet — add one below.</p>')
    form = ('<div class="box"><h2>Add / update an entry</h2>'
            '<form method=post action="/add" enctype="multipart/form-data">'
            '<label>Title</label><input name=title required>'
            '<label>Kind</label><input name=kind placeholder="character / item / tech / ship / place">'
            '<label>Fields (one per line, key = value) — these become the infobox table</label>'
            '<textarea name=fields rows=5 placeholder="class = BC-304&#10;crew = 200"></textarea>'
            '<label>Description (use == Heading == for sections)</label><textarea name=body rows=5></textarea>'
            '<label>Categories (comma-separated)</label><input name=categories>'
            '<label>Image</label><input type=file name=image accept="image/*">'
            '<button type=submit>Save entry</button></form></div>')
    heading = f"Search: {html.escape(query)}" if query else "Your codex"
    count = f'<span class="muted"> &mdash; {len(entries)} entr{"y" if len(entries) == 1 else "ies"}</span>'
    return f'<h1 class="page">{html.escape(heading)}{count}</h1><div style="height:12px"></div>{listing}{form}'


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
            entries = codex.search(q, 100) if q else codex.all_entries()
            return self._page(_render_index(entries, q))
        if path.startswith("/wiki/"):
            entry = codex.get(unquote(path[len("/wiki/"):]))
            if entry is None:
                return self._page("<p>No such entry. <a href=/>Back</a></p>", "Not found")
            return self._page(_render_entry(entry), entry.get("title", "Entry"))
        if path.startswith("/images/"):
            name = os.path.basename(unquote(path))             # basename blocks path traversal
            fpath = os.path.join(codex.base(), "images", name)
            if os.path.isfile(fpath):
                import mimetypes
                with open(fpath, "rb") as f:
                    return self._send(f.read(), 200, mimetypes.guess_type(fpath)[0] or "application/octet-stream")
            return self._send("not found", 404, "text/plain")
        return self._send("not found", 404, "text/plain")

    def _api(self, qs):
        action = (qs.get("action") or [""])[0]
        if action == "parse":
            entry = codex.get((qs.get("page") or [""])[0])
            if entry is None:
                return self._json({"error": {"code": "missingtitle", "info": "page not found"}})
            return self._json({"parse": parse_payload(entry)})
        if action == "query" and (qs.get("list") or [""])[0] == "search":
            limit = int((qs.get("srlimit") or ["10"])[0])
            hits = codex.search((qs.get("srsearch") or [""])[0], limit)
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
        entry = codex.save(title, kind=fields.get("kind", "").strip(), fields=field_map,
                           body=fields.get("body", "").strip(), categories=cats, image=image_path)
        if image_path and os.path.isfile(image_path):
            os.remove(image_path)
        self.send_response(303)                                # redirect to the new entry
        self.send_header("Location", "/wiki/" + entry["slug"])
        self.end_headers()


def make_server(host: str = "127.0.0.1", port: int = 8800) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), _Handler)
