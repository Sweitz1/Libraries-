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
:root{{color-scheme:light dark}}
body{{font-family:system-ui,Segoe UI,Helvetica,Arial,sans-serif;max-width:860px;margin:0 auto;padding:24px;
line-height:1.5;background:#fbfbfd;color:#1c2127}}
@media(prefers-color-scheme:dark){{body{{background:#14171c;color:#e6e9ee}}a{{color:#8ab4ff}}
.box{{background:#1c2127;border-color:#2a2f37}}.ib th{{background:#222834}}}}
a{{color:#2b5bd7;text-decoration:none}}a:hover{{text-decoration:underline}}
h1{{margin:0 0 4px}}.kind{{color:#8a93a3;font-size:14px;margin-bottom:16px}}
.box{{background:#fff;border:1px solid #e2e6ec;border-radius:10px;padding:16px;margin:16px 0}}
.ib{{border-collapse:collapse;width:100%}}.ib th{{text-align:left;background:#eef1f5;width:34%;padding:6px 8px;
vertical-align:top}}.ib td{{padding:6px 8px;vertical-align:top}}.ib tr+tr{{border-top:1px solid #e2e6ec}}
input,textarea,select,button{{font:inherit;padding:8px;border:1px solid #c7ceda;border-radius:8px;
width:100%;box-sizing:border-box;margin:4px 0 12px;background:transparent;color:inherit}}
button{{width:auto;background:#2b3a57;color:#fff;border:0;cursor:pointer;padding:9px 18px}}
label{{font-weight:600;font-size:14px}}.cat{{color:#8a93a3;font-size:14px;margin-top:12px}}
img.art{{max-width:100%;border-radius:10px;margin:8px 0}}.muted{{color:#8a93a3}}
</style></head><body>{body}</body></html>"""


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
def _render_entry(entry: dict) -> str:
    rows = "".join(f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}</td></tr>"
                   for k, v in entry.get("fields", {}).items())
    img = f'<img class="art" src="/{html.escape(entry["image"])}" alt="">' if entry.get("image") else ""
    body = f"<p>{html.escape(entry['body'])}</p>" if entry.get("body") else ""
    cats = ("<div class=cat>Categories: " + ", ".join(html.escape(c) for c in entry["categories"]) + "</div>"
            if entry.get("categories") else "")
    infobox = f'<div class=box>{img}<table class=ib>{rows}</table></div>' if (rows or img) else ""
    return (f'<p><a href="/">&larr; all entries</a></p><h1>{html.escape(entry.get("title", ""))}</h1>'
            f'<div class=kind>{html.escape(entry.get("kind") or "entry")}</div>{infobox}{body}{cats}')


def _render_index(entries: list, query: str = "") -> str:
    items = "".join(f'<li><a href="/wiki/{html.escape(e["slug"])}">{html.escape(e["title"])}</a>'
                    f' <span class=muted>{html.escape(e.get("kind") or "")}</span></li>' for e in entries)
    listing = f"<ul>{items}</ul>" if items else "<p class=muted>No entries yet — add one below.</p>"
    form = ('<div class=box><h2>Add / update an entry</h2>'
            '<form method=post action="/add" enctype="multipart/form-data">'
            '<label>Title</label><input name=title required>'
            '<label>Kind</label><input name=kind placeholder="character / item / tech / ship / place">'
            '<label>Fields (one per line, key = value)</label>'
            '<textarea name=fields rows=5 placeholder="class = BC-304&#10;crew = 200"></textarea>'
            '<label>Body</label><textarea name=body rows=3></textarea>'
            '<label>Categories (comma-separated)</label><input name=categories>'
            '<label>Image</label><input type=file name=image accept="image/*">'
            '<button type=submit>Save entry</button></form></div>')
    search = (f'<form method=get action="/"><input name=q value="{html.escape(query)}" '
              'placeholder="Search your codex…"></form>')
    heading = f"Search: {html.escape(query)}" if query else "Your codex"
    return f"<h1>{html.escape(heading)}</h1>{search}{listing}{form}"


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
