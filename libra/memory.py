"""Long-term memory: facts, preferences and outcomes the agent keeps between conversations.

    libra memory add "My Debian box is called atlas and it's in the office"
    libra memory search "where is the server"
    libra memory forget 3f2a

In chat (app, Discord, terminal) you can say "remember that …", "what do you remember
about …" and "forget …". The autonomous agent also records what its goals produced, so
"what did you find about the falcon budget yesterday?" can be answered from memory.

Stored in ``~/.libra/memory.jsonl``. Search is BM25 over the remembered text, with a small
bonus for recent and often-used memories. Nothing leaves the machine.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import os
import re
import threading

from . import home
from .knowledge import BM25, terms

_LOCK = threading.Lock()


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Memory:
    FILE = "memory.jsonl"

    def __init__(self, path: str | None = None):
        self.path = path or home.path(self.FILE)
        self.items: list[dict] = []
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.items = [json.loads(l) for l in f if l.strip()]

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for it in self.items:
                f.write(json.dumps(it, ensure_ascii=False) + "\n")
        os.replace(tmp, self.path)

    def add(self, text: str, source: str = "user", kind: str = "fact") -> dict:
        """Remember ``text``. A near-identical memory is refreshed instead of duplicated."""
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            raise ValueError("nothing to remember")
        words = set(terms(text))
        with _LOCK:
            for it in self.items:
                other = set(terms(it["text"]))
                if words and other and len(words & other) / len(words | other) >= 0.85:
                    it.update(text=text, updated=_now(), source=source)
                    self._save()
                    return it
            item = {"id": hashlib.sha1(f"{text}{_now()}".encode()).hexdigest()[:6], "text": text, "kind": kind,
                    "source": source, "created": _now(), "updated": _now(), "uses": 0}
            self.items.append(item)
            self._save()
        return item

    def search(self, query: str, k: int = 5, min_cover: float = 0.0) -> list[tuple[float, dict]]:
        """Best memories for ``query``. ``min_cover`` keeps only memories sharing that share of its words."""
        q = terms(query)
        if not q or not self.items:
            return []
        bm = BM25([terms(it["text"]) for it in self.items])
        now = _dt.datetime.now().astimezone()
        out = []
        for score, it in zip(bm.scores(q), self.items):
            if score <= 0:
                continue
            cover = len(set(q) & set(terms(it["text"]))) / len(set(q))
            if cover < min_cover:
                continue
            age_days = (now - _dt.datetime.fromisoformat(it["updated"])).total_seconds() / 86400
            score *= 1 + 0.1 * math.log1p(it.get("uses", 0)) + 0.1 / (1 + age_days / 30)
            out.append((score, it))
        out.sort(key=lambda x: -x[0])
        return out[:k]

    def recall(self, query: str, k: int = 5) -> list[dict]:
        found = [it for _, it in self.search(query, k, min_cover=0.5)]
        if found:
            with _LOCK:
                for it in found:
                    it["uses"] = it.get("uses", 0) + 1
                self._save()
        return found

    def forget(self, what: str) -> list[dict]:
        """Forget by id, or every memory that strongly matches ``what``."""
        with _LOCK:
            gone = [it for it in self.items if it["id"] == what.strip()]
            if not gone:
                gone = [it for _, it in self.search(what, 50, min_cover=0.75)]
            ids = {it["id"] for it in gone}
            self.items = [it for it in self.items if it["id"] not in ids]
            self._save()
        return gone


def describe(items: list[dict]) -> str:
    if not items:
        return "I don't remember anything about that."
    return "\n".join(f"- {it['text']}  ({it['updated'][:10]}, #{it['id']})" for it in items)


# -------------------------------------------------- chat phrases for memory
REMEMBER_RE = re.compile(r"^\s*(?:please\s+)?(?:remember|note|keep in mind|don'?t forget)\s+(?:that\s+)?(?P<x>.+)$",
                         re.I | re.S)
RECALL_RE = re.compile(r"^\s*(?:what do you (?:remember|know) about|do you remember|recall|what did (?:you|i) "
                       r"(?:find|learn|say|tell you) about)\s+(?P<x>.+?)\s*\??\s*$", re.I | re.S)
FORGET_RE = re.compile(r"^\s*forget\s+(?:about\s+|that\s+)?(?P<x>.+?)\s*$", re.I | re.S)
