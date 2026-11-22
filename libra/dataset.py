"""Build a clean training dataset from your files, web pages and public-domain books.

Output is a folder that ``libra train`` reads directly:

* ``text.jsonl``  one ``{"text": ...}`` document per line
* ``chat.jsonl``  one ``{"messages": [...]}`` conversation per line
* ``stats.json``  sizes and a suggested training command
"""
from __future__ import annotations

import csv
import hashlib
import html
import io
import json
import os
import re
import urllib.request
from dataclasses import dataclass, field
from html.parser import HTMLParser

TEXT_EXT = {".txt", ".md", ".rst", ".text"}
HTML_EXT = {".html", ".htm", ".xhtml"}
JSON_EXT = {".json", ".jsonl"}
CSV_EXT = {".csv", ".tsv"}
USER_AGENT = "libra-dataset/0.2"

USER_KEYS = ("question", "prompt", "input", "user", "instruction", "query", "q")
ASSISTANT_KEYS = ("answer", "response", "output", "assistant", "completion", "reply", "a")
ROLE_ALIASES = {"human": "user", "user": "user", "gpt": "assistant", "assistant": "assistant", "bot": "assistant",
                "model": "assistant", "chatgpt": "assistant", "system": "system"}


# ---------------------------------------------------------------- cleaning
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("﻿", "")
    text = _CONTROL.sub("", text)
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_gutenberg(text: str) -> str:
    """Remove the Project Gutenberg licence header and footer."""
    start = re.search(r"\*\*\* ?START OF (THE|THIS) PROJECT GUTENBERG[^\n]*", text, re.I)
    if start:
        text = text[start.end():]
    end = re.search(r"\*\*\* ?END OF (THE|THIS) PROJECT GUTENBERG", text, re.I)
    if end:
        text = text[: end.start()]
    return text


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "noscript", "nav", "footer", "header", "aside", "svg", "form", "head"}
    BLOCK = {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section", "article",
             "blockquote", "pre", "table", "ul", "ol"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag in self.BLOCK:
            self.parts.append("\n\n" if tag != "br" else "\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        elif tag in self.BLOCK:
            self.parts.append("\n\n")

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def html_to_text(markup: str) -> str:
    parser = _HTMLText()
    parser.feed(markup)
    parser.close()
    text = html.unescape("".join(parser.parts))
    text = re.sub(r"[ \t]+", " ", text)
    return clean_text("\n".join(line.strip() for line in text.split("\n")))


# ------------------------------------------------------------ conversations
def _msg(role, content):
    role = ROLE_ALIASES.get(str(role).lower(), "user")
    return {"role": role, "content": clean_text(str(content or ""))}


def _pick(record: dict, keys) -> str | None:
    lower = {k.lower(): v for k, v in record.items()}
    for k in keys:
        if lower.get(k):
            return str(lower[k])
    return None


def _chatgpt_export(conv: dict) -> list[dict]:
    """Follow a ChatGPT ``conversations.json`` entry from its current node back to the root."""
    mapping = conv.get("mapping") or {}
    node_id = conv.get("current_node")
    messages = []
    while node_id and node_id in mapping:
        node = mapping[node_id]
        m = node.get("message") or {}
        parts = (m.get("content") or {}).get("parts") or []
        text = "".join(p for p in parts if isinstance(p, str))
        role = (m.get("author") or {}).get("role")
        if text.strip() and role in ("user", "assistant", "system"):
            messages.append(_msg(role, text))
        node_id = node.get("parent")
    return list(reversed(messages))


def record_to_item(record) -> tuple[str, object] | None:
    """Map one JSON record to ``("chat", messages)`` or ``("text", str)``."""
    if isinstance(record, str):
        return "text", record
    if not isinstance(record, dict):
        return None
    if isinstance(record.get("messages"), list):
        msgs = [_msg(m.get("role"), m.get("content")) for m in record["messages"] if isinstance(m, dict)]
        return "chat", msgs
    if isinstance(record.get("conversations"), list):  # ShareGPT style
        msgs = [_msg(m.get("from") or m.get("role"), m.get("value") or m.get("content"))
                for m in record["conversations"] if isinstance(m, dict)]
        return "chat", msgs
    if "mapping" in record:
        return "chat", _chatgpt_export(record)
    lower = {k.lower(): v for k, v in record.items()}
    if lower.get("instruction") and (lower.get("output") or lower.get("response")):  # Alpaca style
        user = str(lower["instruction"]) + (f"\n\n{lower['input']}" if lower.get("input") else "")
        return "chat", [_msg("user", user), _msg("assistant", lower.get("output") or lower["response"])]
    user, assistant = _pick(record, USER_KEYS), _pick(record, ASSISTANT_KEYS)
    if user and assistant:
        return "chat", [_msg("user", user), _msg("assistant", assistant)]
    for key in ("text", "content", "body"):
        if isinstance(record.get(key), str):
            return "text", record[key]
    return None


# --------------------------------------------------------------- the builder
@dataclass
class Dataset:
    texts: list[str] = field(default_factory=list)
    chats: list[list[dict]] = field(default_factory=list)
    min_chars: int = 40
    skipped: int = 0
    duplicates: int = 0
    _seen: set = field(default_factory=set)

    def _fresh(self, key: str) -> bool:
        digest = hashlib.sha1(key.encode("utf-8")).digest()
        if digest in self._seen:
            self.duplicates += 1
            return False
        self._seen.add(digest)
        return True

    def add_text(self, text: str, source: str = "") -> None:
        text = clean_text(text)
        if len(text) < self.min_chars:
            self.skipped += 1
            return
        kept = []
        for para in text.split("\n\n"):  # drop paragraphs seen before (boilerplate, repeats)
            norm = re.sub(r"\s+", " ", para).strip().lower()
            if len(norm) < 30 or self._fresh(norm):
                kept.append(para)
        text = "\n\n".join(kept).strip()
        if len(text) >= self.min_chars:
            self.texts.append(text)
        else:
            self.skipped += 1

    def add_chat(self, messages: list[dict]) -> None:
        messages = [m for m in messages if m["content"]]
        if not any(m["role"] == "assistant" for m in messages) or not any(m["role"] == "user" for m in messages):
            self.skipped += 1
            return
        if self._fresh(json.dumps(messages, sort_keys=True)):
            self.chats.append(messages)

    def add_item(self, item) -> None:
        if item is None:
            self.skipped += 1
        elif item[0] == "chat":
            self.add_chat(item[1])
        else:
            self.add_text(item[1])

    # ------------------------------------------------------------ sources
    def add_path(self, path: str, log=print) -> None:
        if os.path.isdir(path):
            for root, dirs, files in os.walk(path):
                dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                for name in sorted(files):
                    self.add_file(os.path.join(root, name), log)
        else:
            self.add_file(path, log)

    def add_file(self, path: str, log=print) -> None:
        ext = os.path.splitext(path)[1].lower()
        from .documents import DOC_EXT

        if ext in DOC_EXT:  # docx, pptx, xlsx, epub, odt/odp/ods, rtf
            from .documents import DocumentError, extract

            try:
                self.add_text(extract(path))
            except DocumentError as exc:
                log(f"  skipped {os.path.basename(path)}: {exc}")
            return
        if ext not in TEXT_EXT | HTML_EXT | JSON_EXT | CSV_EXT:
            return
        with open(path, encoding="utf-8", errors="replace") as f:
            raw = f.read()
        before = (len(self.texts), len(self.chats))
        if ext in TEXT_EXT:
            self.add_text(strip_gutenberg(raw) if "PROJECT GUTENBERG" in raw[:5000].upper() else raw)
        elif ext in HTML_EXT:
            self.add_text(html_to_text(raw))
        elif ext in JSON_EXT:
            self._add_json(raw, path)
        else:
            self._add_csv(raw, "\t" if ext == ".tsv" else ",")
        log(f"  {path}: +{len(self.texts) - before[0]} docs, +{len(self.chats) - before[1]} chats")

    def _add_json(self, raw: str, path: str) -> None:
        stripped = raw.lstrip()
        if stripped.startswith("[") or (stripped.startswith("{") and not path.endswith(".jsonl")):
            try:
                data = json.loads(raw)
                for record in data if isinstance(data, list) else [data]:
                    self.add_item(record_to_item(record))
                return
            except json.JSONDecodeError:
                pass  # fall back to JSON lines
        for line in raw.splitlines():
            line = line.strip()
            if line:
                try:
                    self.add_item(record_to_item(json.loads(line)))
                except json.JSONDecodeError:
                    self.skipped += 1

    def _add_csv(self, raw: str, delimiter: str) -> None:
        reader = csv.DictReader(io.StringIO(raw), delimiter=delimiter)
        for row in reader:
            item = record_to_item({k: v for k, v in row.items() if k})
            if item is None:  # no recognised columns: join the cells as text
                item = ("text", " ".join(v for v in row.values() if isinstance(v, str)))
            self.add_item(item)

    def add_url(self, url: str, log=print) -> None:
        raw, ctype = fetch(url)
        if "html" in ctype or raw.lstrip()[:15].lower().startswith(("<!doctype", "<html")):
            self.add_text(html_to_text(raw))
        else:
            self.add_text(strip_gutenberg(raw))
        log(f"  {url}: {len(raw):,} characters downloaded")

    def add_gutenberg(self, book_id: int, log=print) -> None:
        url = f"https://www.gutenberg.org/cache/epub/{int(book_id)}/pg{int(book_id)}.txt"
        raw, _ = fetch(url)
        self.add_text(strip_gutenberg(raw))
        log(f"  gutenberg #{book_id}: {len(raw):,} characters")

    # ------------------------------------------------------------- output
    def stats(self) -> dict:
        chars = sum(len(t) for t in self.texts) + sum(len(m["content"]) for c in self.chats for m in c)
        tokens = chars // 4  # rough: ~4 characters per token for English with a few thousand merges
        return {"documents": len(self.texts), "conversations": len(self.chats), "characters": chars,
                "estimated_tokens": tokens, "skipped": self.skipped, "duplicates_removed": self.duplicates,
                "suggestion": suggest(tokens)}

    def save(self, out_dir: str) -> dict:
        os.makedirs(out_dir, exist_ok=True)
        for name, rows in (("text.jsonl", [{"text": t} for t in self.texts]),
                           ("chat.jsonl", [{"messages": c} for c in self.chats])):
            path = os.path.join(out_dir, name)
            if rows:
                with open(path, "w", encoding="utf-8") as f:
                    for row in rows:
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
            elif os.path.exists(path):
                os.remove(path)
        stats = self.stats()
        stats["suggestion"]["command"] = stats["suggestion"]["command"].replace("DATA", out_dir)
        with open(os.path.join(out_dir, "stats.json"), "w", encoding="utf-8") as f:
            json.dump(stats, f, indent=2)
        return stats


def fetch(url: str, timeout: int = 60) -> tuple[str, str]:
    """Download one page you asked for, with the same guard as every other fetch: no private or local
    addresses (unless --allow-private), every redirect checked, environment proxies only when trusted."""
    from .web import Fetcher

    if not url.startswith(("http://", "https://")):
        raise ValueError(f"only http(s) URLs are supported: {url}")
    fetcher = Fetcher(user_agent=USER_AGENT, timeout=timeout, max_bytes=50_000_000, respect_robots=False,
                      allow_private=os.environ.get("LIBRA_ALLOW_PRIVATE") == "1")
    r = fetcher.get(url)  # a single download you chose, like a browser: robots.txt is for crawlers
    return r.text, r.content_type


PRESETS = [  # (max tokens, name, flags, batch*ctx)
    (300_000, "tiny", "--layers 2 --embd 96 --heads 4 --ctx 64 --vocab 512", 16 * 64),
    (5_000_000, "small", "--layers 4 --embd 128 --heads 4 --ctx 128 --vocab 1024", 16 * 128),
    (50_000_000, "medium", "--layers 6 --embd 256 --heads 8 --ctx 256 --vocab 4096", 16 * 256),
    (float("inf"), "large", "--layers 8 --embd 384 --heads 6 --ctx 256 --vocab 8192 --device gpu", 16 * 256),
]


def suggest(tokens: int) -> dict:
    for limit, name, flags, per_step in PRESETS:
        if tokens <= limit:
            break
    epochs = 20 if tokens < 300_000 else 5 if tokens < 5_000_000 else 1
    steps = int(min(200_000, max(300, epochs * tokens / per_step)))
    return {"preset": name, "steps": steps,
            "command": f"libra train DATA --out checkpoints/{name} {flags} --steps {steps}"}
