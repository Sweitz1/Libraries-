"""Web fetching, HTML parsing, CSS selectors, scraping and crawling.

Standard library only. The fetcher is polite by default: it obeys robots.txt,
waits between requests to the same host, caps download size and refuses
private/loopback addresses unless ``allow_private=True``.
"""
from __future__ import annotations

import functools
import http.client
import ipaddress
import json
import re
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
import urllib.robotparser
from collections import deque
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit, urlunsplit

USER_AGENT = "LibraBot/0.3"
SKIP_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".ico", ".bmp", ".pdf", ".zip", ".gz", ".tar", ".rar",
    ".7z", ".mp3", ".mp4", ".avi", ".mov", ".mkv", ".webm", ".wav", ".ogg", ".exe", ".dmg", ".apk", ".iso",
    ".css", ".js", ".woff", ".woff2", ".ttf", ".eot", ".xml", ".rss", ".json",
}


class FetchError(RuntimeError):
    pass


@dataclass
class Response:
    url: str
    status: int
    content_type: str
    text: str
    headers: dict = field(default_factory=dict)

    @property
    def is_html(self) -> bool:
        return "html" in self.content_type or self.text.lstrip()[:15].lower().startswith(("<!doctype", "<html"))


def normalize_url(url: str, base: str | None = None) -> str | None:
    """Absolute http(s) URL without fragment, or None if it isn't a web URL."""
    url = (url or "").strip()
    if base:
        url = urljoin(base, url)
    parts = urlsplit(url)
    if parts.scheme.lower() not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, ""))


def _blocked_ip(ip_text: str) -> bool:
    """True for anything that isn't a public address: private, loopback, link-local, CGNAT (100.64/10,
    used by Tailscale), multicast, reserved, unspecified — including IPv4 addresses mapped into IPv6."""
    ip = ipaddress.ip_address(ip_text.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast


def _is_private_host(host: str) -> bool:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return False  # let the request fail normally
    return any(_blocked_ip(info[4][0]) for info in infos)


def _resolves(host: str) -> bool:
    try:
        return bool(socket.getaddrinfo(host, None))
    except socket.gaierror:
        return False


class PrivateAddressError(OSError):
    pass


def _safe_connect(host: str, port: int, timeout, source_address, allow_private: bool) -> socket.socket:
    """Resolve ``host`` once and connect to an address that passed the check, so a second DNS answer
    (DNS rebinding) can't send the request somewhere else."""
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    last_error: OSError | None = None
    refused = []
    for family, kind, proto, _, addr in infos:
        if not allow_private and _blocked_ip(addr[0]):
            refused.append(addr[0])
            continue
        sock = socket.socket(family, kind, proto)
        try:
            if timeout is not None and timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                sock.settimeout(timeout)
            if source_address:
                sock.bind(source_address)
            sock.connect(addr)
            return sock
        except OSError as exc:
            sock.close()
            last_error = exc
    if refused and last_error is None:
        raise PrivateAddressError(f"refusing private or local address {host} ({', '.join(sorted(set(refused)))}); "
                                  "use allow_private / --allow-private")
    raise last_error or OSError(f"could not connect to {host}")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, allow_private: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_private = allow_private

    def connect(self):
        self.sock = _safe_connect(self.host, self.port, self.timeout, self.source_address, self.allow_private)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, allow_private: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.allow_private = allow_private

    def connect(self):
        sock = _safe_connect(self.host, self.port, self.timeout, self.source_address, self.allow_private)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)  # certificate checked for the name


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, allow_private: bool):
        super().__init__()
        self.allow_private = allow_private

    def http_open(self, req):
        return self.do_open(functools.partial(_PinnedHTTPConnection, allow_private=self.allow_private), req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, allow_private: bool):
        super().__init__(context=ssl.create_default_context())
        self.allow_private = allow_private

    def https_open(self, req):
        return self.do_open(functools.partial(_PinnedHTTPSConnection, allow_private=self.allow_private), req,
                            context=self._context)


def _proxy_trusted() -> bool:
    """Environment proxies are used only when ``LIBRA_USE_PROXY=1`` says they're trusted.

    Through a proxy, the proxy resolves the name and makes the connection, so Libra can't pin the
    address it checked. A name that only the proxy can resolve (an internal host) would get past a check
    done with local DNS. So by default fetches connect directly and ignore HTTP(S)_PROXY; set
    LIBRA_USE_PROXY=1 only for a proxy that enforces its own destination rules."""
    return os.environ.get("LIBRA_USE_PROXY") == "1"


def _uses_proxy(url: str) -> bool:
    """True if this fetch goes through a proxy: a trusted one is configured (HTTP(S)_PROXY, honouring
    NO_PROXY, with LIBRA_USE_PROXY=1)."""
    if not _proxy_trusted():
        return False
    parts = urlsplit(url)
    proxies = urllib.request.getproxies()
    if parts.scheme not in proxies:
        return False
    return not urllib.request.proxy_bypass(parts.hostname or "")


class Fetcher:
    def __init__(self, user_agent: str = USER_AGENT, timeout: float = 20, max_bytes: int = 10_000_000,
                 respect_robots: bool = True, delay: float = 0.0, allow_private: bool = False):
        self.user_agent = user_agent
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.respect_robots = respect_robots
        self.delay = delay
        self.allow_private = allow_private
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        fetcher = self

        class _Redirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                fetcher._check_url(newurl)  # every hop gets the same safety checks
                return super().redirect_request(req, fp, code, msg, headers, newurl)

        # Direct connections check the address they actually connect to (no DNS-rebinding window).
        self._direct = urllib.request.build_opener(_Redirect, urllib.request.ProxyHandler({}),
                                                   _PinnedHTTPHandler(allow_private), _PinnedHTTPSHandler(allow_private))
        # Through a trusted proxy (LIBRA_USE_PROXY=1) the proxy makes the connection, so the name check in
        # _check_url is all Libra can do; it then also refuses names that don't resolve here.
        self._proxied = urllib.request.build_opener(_Redirect)

    def _open(self, req):
        opener = self._proxied if _uses_proxy(req.full_url) else self._direct
        try:
            return opener.open(req, timeout=self.timeout)
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, PrivateAddressError):
                raise FetchError(str(exc.reason)) from None
            raise

    # -------------------------------------------------------------- checks
    def _check_url(self, url: str) -> str:
        norm = normalize_url(url)
        if norm is None:
            raise FetchError(f"only http(s) URLs are supported: {url}")
        host = urlsplit(norm).hostname or ""
        if not self.allow_private and _is_private_host(host):
            raise FetchError(f"refusing private or local address {host} (use allow_private / --allow-private)")
        if not self.allow_private and _uses_proxy(norm) and not _resolves(host):
            # the proxy might resolve it to an internal host that Libra can't see: fail closed
            raise FetchError(f"refusing {host}: it doesn't resolve here, so it can't be checked before the proxy "
                             "connects to it")
        return norm

    def _robots_for(self, url: str):
        parts = urlsplit(url)
        key = f"{parts.scheme}://{parts.netloc}"
        if key not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._check_url(key + "/robots.txt")
                req = urllib.request.Request(key + "/robots.txt", headers={"User-Agent": self.user_agent})
                with self._open(req) as r:
                    rp.parse(r.read(500_000).decode("utf-8", "replace").splitlines())
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403):
                    rp.disallow_all = True
                else:
                    rp.allow_all = True
            except Exception:
                rp.allow_all = True
            self._robots[key] = rp
        return self._robots[key]

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        return self._robots_for(url).can_fetch(self.user_agent, url)

    def crawl_delay(self, url: str) -> float:
        if not self.respect_robots:
            return self.delay
        try:
            robot_delay = self._robots_for(url).crawl_delay(self.user_agent)
        except Exception:
            robot_delay = None
        return max(self.delay, float(robot_delay or 0))

    # --------------------------------------------------------------- fetch
    def get(self, url: str) -> Response:
        url = self._check_url(url)
        if not self.allowed(url):
            raise FetchError(f"blocked by robots.txt: {url}")
        host = urlsplit(url).netloc
        wait = self._last.get(host, 0) + self.crawl_delay(url) - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        return self.request("GET", url, {"Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5"})

    def request(self, method: str, url: str, headers: dict | None = None, data: bytes | None = None,
                raise_for_status: bool = True) -> Response:
        """Any HTTP method, with the same address checks (robots.txt is only checked by :meth:`get`)."""
        url = self._check_url(url)
        host = urlsplit(url).netloc
        req = urllib.request.Request(url, data=data, method=method.upper(),
                                     headers={"User-Agent": self.user_agent, **(headers or {})})
        try:
            with self._open(req) as r:
                raw = r.read(self.max_bytes + 1)[: self.max_bytes]
                ctype = (r.headers.get("Content-Type") or "").lower()
                charset = r.headers.get_content_charset()
                status, final, headers = r.status, r.geturl(), dict(r.headers.items())
        except urllib.error.HTTPError as exc:
            if raise_for_status:
                raise FetchError(f"HTTP {exc.code} for {url}") from None
            raw = exc.read(self.max_bytes)
            ctype = (exc.headers.get("Content-Type") or "").lower()
            charset = exc.headers.get_content_charset()
            status, final, headers = exc.code, url, dict(exc.headers.items())
        except urllib.error.URLError as exc:
            raise FetchError(f"could not fetch {url}: {exc.reason}") from None
        except (TimeoutError, socket.timeout):
            raise FetchError(f"timed out fetching {url}") from None
        finally:
            self._last[host] = time.monotonic()
        if not charset:
            m = re.search(rb"""<meta[^>]+charset=["']?([\w-]+)""", raw[:4096], re.I)
            charset = m.group(1).decode() if m else "utf-8"
        try:
            text = raw.decode(charset, errors="replace")
        except LookupError:
            text = raw.decode("utf-8", errors="replace")
        return Response(final, status, ctype, text, headers)


# ------------------------------------------------------------------- DOM
VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
             "track", "wbr"}
AUTO_CLOSE = {"p", "li", "tr", "td", "th", "option", "dt", "dd"}
SKIP_TEXT = {"script", "style", "noscript", "template", "svg", "head"}
NOISE = {"nav", "footer", "header", "aside", "form"}
BLOCK_TAGS = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article", "main",
              "blockquote", "pre", "table", "ul", "ol", "dl", "dt", "dd", "figure", "figcaption", "hr"}


class Node:
    __slots__ = ("tag", "attrs", "children", "parent")

    def __init__(self, tag: str, attrs: dict | None = None, parent: "Node | None" = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []
        self.parent = parent

    def __repr__(self) -> str:
        return f"<{self.tag} {self.attrs}>"

    def get(self, name: str, default=None):
        return self.attrs.get(name, default)

    @property
    def classes(self) -> list[str]:
        return (self.attrs.get("class") or "").split()

    def iter(self):
        """All descendant elements in document order."""
        stack = list(reversed([c for c in self.children if isinstance(c, Node)]))
        while stack:
            node = stack.pop()
            yield node
            stack.extend(reversed([c for c in node.children if isinstance(c, Node)]))

    def find_all(self, tag: str) -> list["Node"]:
        return [n for n in self.iter() if n.tag == tag]

    def find(self, tag: str) -> "Node | None":
        return next((n for n in self.iter() if n.tag == tag), None)

    @property
    def text(self) -> str:
        """Inline text with whitespace collapsed."""
        parts = []
        stack = [self]
        while stack:
            node = stack.pop()
            if isinstance(node, str):
                parts.append(node)
            elif node.tag not in SKIP_TEXT or node is self:
                stack.extend(reversed(node.children))
        return re.sub(r"\s+", " ", "".join(parts)).strip()

    def block_text(self, skip_noise: bool = True) -> str:
        """Readable text with paragraph breaks, without scripts (and, by default, menus/footers)."""
        out: list[str] = []

        def walk(node):
            for child in node.children:
                if isinstance(child, str):
                    out.append(child)
                    continue
                if child.tag in SKIP_TEXT or (skip_noise and child.tag in NOISE):
                    continue
                block = child.tag in BLOCK_TAGS
                if block:
                    out.append("\n\n")
                if child.tag == "pre":
                    out.append(child.text)
                else:
                    walk(child)
                if block:
                    out.append("\n\n")

        walk(self)
        text = re.sub(r"[ \t\r\f\v]+", " ", "".join(out))
        text = "\n".join(line.strip() for line in text.split("\n"))
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    def select(self, selector: str) -> list["Node"]:
        groups = [_parse_selector(s) for s in _split_groups(selector)]
        return [n for n in self.iter() if any(_matches(n, g, len(g[0]) - 1) for g in groups)]

    def select_one(self, selector: str) -> "Node | None":
        found = self.select(selector)
        return found[0] if found else None


class _TreeBuilder(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Node("#document")
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        if tag in AUTO_CLOSE and self.stack[-1].tag == tag:
            self.stack.pop()
        node = Node(tag, {k.lower(): (v if v is not None else "") for k, v in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if tag not in VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag.lower() not in VOID_TAGS and self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag):
        tag = tag.lower()
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def parse_html(markup: str) -> Node:
    builder = _TreeBuilder()
    builder.feed(markup)
    builder.close()
    return builder.root


# ------------------------------------------------------------ CSS selectors
# Supported: tag, *, #id, .class, [attr], [attr=v], [attr~=v], [attr^=v],
# [attr$=v], [attr*=v], [attr|=v], :first-child, :last-child, :nth-child(n),
# descendant (space) and child (>) combinators, and comma-separated groups.
_ATTR = re.compile(r"""^\s*([\w:-]+)\s*(?:([~^$*|]?=)\s*(?:"([^"]*)"|'([^']*)'|([^\s"']+)))?\s*$""")


def _split_groups(selector: str) -> list[str]:
    groups, depth, quote, cur = [], 0, None, []
    for ch in selector:
        if quote:
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
        elif ch in "[(":
            depth += 1
        elif ch in "])":
            depth -= 1
        elif ch == "," and depth == 0:
            groups.append("".join(cur).strip())
            cur = []
            continue
        cur.append(ch)
    groups.append("".join(cur).strip())
    if not all(groups):
        raise ValueError(f"empty selector in {selector!r}")
    return groups


def _tokenize(selector: str) -> list[str]:
    tokens, depth, quote, cur = [], 0, None, []

    def flush():
        if cur:
            tokens.append("".join(cur))
            cur.clear()

    for ch in selector:
        if quote:
            cur.append(ch)
            quote = None if ch == quote else quote
        elif ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "[(":
            depth += 1
            cur.append(ch)
        elif ch in "])":
            depth -= 1
            cur.append(ch)
        elif depth == 0 and ch == ">":
            flush()
            tokens.append(">")
        elif depth == 0 and ch.isspace():
            flush()
        else:
            cur.append(ch)
    flush()
    return tokens


def _parse_compound(s: str):
    m = re.match(r"\*|[a-zA-Z][\w-]*", s)
    tag = None if not m or m.group() == "*" else m.group().lower()
    pos = m.end() if m else 0
    conds = []
    while pos < len(s):
        c = s[pos]
        if c in "#.":
            m = re.compile(r"[\w-]+").match(s, pos + 1)
            if not m:
                raise ValueError(f"bad selector {s!r}")
            conds.append((c, m.group()))
            pos = m.end()
        elif c == "[":
            end = s.find("]", pos)
            am = _ATTR.match(s[pos + 1 : end]) if end > 0 else None
            if not am:
                raise ValueError(f"bad attribute selector in {s!r}")
            value = next((g for g in am.groups()[2:] if g is not None), None)
            conds.append(("[", (am.group(1).lower(), am.group(2), value)))
            pos = end + 1
        elif c == ":":
            m = re.compile(r":(first-child|last-child|nth-child\((\d+)\))").match(s, pos)
            if not m:
                raise ValueError(f"unsupported pseudo-class in {s!r}")
            conds.append((":", (m.group(1).split("(")[0], int(m.group(2) or 0))))
            pos = m.end()
        else:
            raise ValueError(f"bad selector {s!r}")
    return tag, conds


def _parse_selector(selector: str):
    tokens = _tokenize(selector)
    compounds, combinators, expect = [], [], True
    for tok in tokens:
        if tok == ">":
            if expect:
                raise ValueError(f"bad selector {selector!r}")
            combinators.append(">")
            expect = True
        else:
            if not expect:
                combinators.append(" ")
            compounds.append(_parse_compound(tok))
            expect = False
    if expect:
        raise ValueError(f"bad selector {selector!r}")
    return compounds, combinators


def _element_siblings(node: Node) -> list[Node]:
    return [c for c in node.parent.children if isinstance(c, Node)] if node.parent else [node]


def _match_compound(node: Node, compound) -> bool:
    tag, conds = compound
    if tag and node.tag != tag:
        return False
    for kind, val in conds:
        if kind == "#" and node.attrs.get("id") != val:
            return False
        if kind == "." and val not in node.classes:
            return False
        if kind == "[":
            name, op, want = val
            have = node.attrs.get(name)
            if have is None:
                return False
            if op == "=" and have != want:
                return False
            if op == "~=" and want not in have.split():
                return False
            if op == "^=" and not have.startswith(want):
                return False
            if op == "$=" and not have.endswith(want):
                return False
            if op == "*=" and want not in have:
                return False
            if op == "|=" and not (have == want or have.startswith(want + "-")):
                return False
        if kind == ":":
            sibs = _element_siblings(node)
            name, n = val
            if name == "first-child" and sibs[0] is not node:
                return False
            if name == "last-child" and sibs[-1] is not node:
                return False
            if name == "nth-child" and (n < 1 or n > len(sibs) or sibs[n - 1] is not node):
                return False
    return True


def _matches(node: Node, parsed, idx: int) -> bool:
    compounds, combinators = parsed
    if not _match_compound(node, compounds[idx]):
        return False
    if idx == 0:
        return True
    parent = node.parent
    if combinators[idx - 1] == ">":
        return parent is not None and parent.tag != "#document" and _matches(parent, parsed, idx - 1)
    while parent is not None and parent.tag != "#document":
        if _matches(parent, parsed, idx - 1):
            return True
        parent = parent.parent
    return False


# --------------------------------------------------------------- scraping
def _main_node(doc: Node) -> Node:
    for sel in ("article", "main", "[role=main]", "body"):
        found = doc.select_one(sel)
        if found is not None:
            return found
    return doc


def extract(markup: str, base_url: str = "", selectors: list[str] | None = None) -> dict:
    """Structured data from an HTML page."""
    doc = parse_html(markup)
    meta = {}
    for m in doc.find_all("meta"):
        key = m.get("name") or m.get("property")
        if key and m.get("content"):
            meta[key.lower()] = m.get("content")
    title_node = doc.find("title")
    title = title_node.text if title_node else meta.get("og:title", "")
    links, seen = [], set()
    for a in doc.find_all("a"):
        url = normalize_url(a.get("href", ""), base_url) if base_url else a.get("href")
        if url and url not in seen:
            seen.add(url)
            links.append({"url": url, "text": a.text})
    images = []
    for img in doc.find_all("img"):
        src = img.get("src") or img.get("data-src")
        if src:
            images.append({"src": urljoin(base_url, src) if base_url else src, "alt": img.get("alt", "")})
    tables = []
    for table in doc.find_all("table"):
        rows = [[cell.text for cell in tr.iter() if cell.tag in ("td", "th")] for tr in table.find_all("tr")]
        rows = [r for r in rows if r]
        if rows:
            tables.append(rows)
    json_ld = []
    for s in doc.select('script[type="application/ld+json"]'):
        try:
            json_ld.append(json.loads("".join(c for c in s.children if isinstance(c, str))))
        except json.JSONDecodeError:
            pass
    html_node = doc.find("html")
    result = {
        "url": base_url,
        "title": title,
        "description": meta.get("description") or meta.get("og:description", ""),
        "lang": html_node.get("lang", "") if html_node else "",
        "headings": [{"level": int(n.tag[1]), "text": n.text}
                     for n in doc.iter() if re.fullmatch(r"h[1-6]", n.tag) and n.text],
        "text": _main_node(doc).block_text(),
        "links": links,
        "images": images,
        "tables": tables,
        "json_ld": json_ld,
        "meta": meta,
    }
    if selectors:
        result["selected"] = {sel: [n.text for n in doc.select(sel)] for sel in selectors}
    return result


def scrape(url: str, selectors: list[str] | None = None, fetcher: Fetcher | None = None) -> dict:
    fetcher = fetcher or Fetcher()
    resp = fetcher.get(url)
    if not resp.is_html:
        return {"url": resp.url, "title": "", "description": "", "lang": "", "headings": [], "text": resp.text,
                "links": [], "images": [], "tables": [], "json_ld": [], "meta": {}, "content_type": resp.content_type}
    result = extract(resp.text, resp.url, selectors)
    result["content_type"] = resp.content_type
    return result


def to_markdown(result: dict) -> str:
    parts = []
    if result.get("title"):
        parts.append(f"# {result['title']}")
    if result.get("url"):
        parts.append(f"<{result['url']}>")
    if result.get("description"):
        parts.append(f"> {result['description']}")
    if result.get("text"):
        parts.append(result["text"])
    for table in result.get("tables", []):
        width = max(len(r) for r in table)
        rows = [r + [""] * (width - len(r)) for r in table]
        lines = ["| " + " | ".join(rows[0]) + " |", "|" + " --- |" * width]
        lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
        parts.append("\n".join(lines))
    for sel, values in (result.get("selected") or {}).items():
        parts.append(f"**{sel}**\n" + "\n".join(f"- {v}" for v in values))
    return "\n\n".join(parts)


# --------------------------------------------------------------- crawling
@dataclass
class Page:
    url: str
    depth: int
    title: str
    text: str
    links: list[str]
    status: int


def crawl(start_url: str, max_pages: int = 20, max_depth: int = 2, delay: float = 1.0, same_site: bool = True,
          include: str | None = None, exclude: str | None = None, fetcher: Fetcher | None = None,
          on_error=None):
    """Breadth-first crawl yielding :class:`Page` objects (HTML pages only)."""
    fetcher = fetcher or Fetcher(delay=delay)
    start = normalize_url(start_url)
    if start is None:
        raise ValueError(f"not a web URL: {start_url}")
    host = urlsplit(start).netloc
    inc = re.compile(include) if include else None
    exc = re.compile(exclude) if exclude else None
    queue, seen, hashes, count = deque([(start, 0)]), {start}, set(), 0

    def wanted(url: str) -> bool:
        parts = urlsplit(url)
        if same_site and parts.netloc != host:
            return False
        ext = parts.path.rsplit(".", 1)[-1].lower() if "." in parts.path.rsplit("/", 1)[-1] else ""
        if ext and f".{ext}" in SKIP_EXTENSIONS:
            return False
        if inc and not inc.search(url):
            return False
        if exc and exc.search(url):
            return False
        try:
            return fetcher.allowed(url)  # skip robots.txt-disallowed pages quietly
        except Exception:
            return True

    while queue and count < max_pages:
        url, depth = queue.popleft()
        try:
            resp = fetcher.get(url)
        except FetchError as err:
            if on_error:
                on_error(url, err)
            continue
        if not resp.is_html:
            continue
        data = extract(resp.text, resp.url)
        digest = hash(data["text"])
        if digest in hashes:  # same content under another URL
            continue
        hashes.add(digest)
        links = [link["url"] for link in data["links"]]
        count += 1
        yield Page(resp.url, depth, data["title"], data["text"], links, resp.status)
        if depth < max_depth:
            for link in links:
                if link not in seen and wanted(link):
                    seen.add(link)
                    queue.append((link, depth + 1))
