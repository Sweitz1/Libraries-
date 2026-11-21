"""Grounded answers from your own files: a workspace with search, citations,
corrections and rules.

* Files you add are split into passages and indexed with BM25 (written here).
* Answers are built only from those passages, sentence by sentence, and every
  sentence is cited with its file and line range, or its media timestamp.
* If nothing relevant is found, the answer says so instead of guessing.
* If the question matches different files about equally well, it asks which
  one you mean before answering.
* Corrections and rules are saved in the workspace and apply to every later
  answer. ``export_training`` turns them, plus your files, into training data
  so a model trained with ``libra train`` picks up your tone.

Images, audio and video are not decoded (Libra has no vision or speech
model). Put a transcript or description next to the file with the same name,
for example ``talk.mp4`` + ``talk.vtt`` or ``photo.jpg`` + ``photo.txt``. It
is then indexed and cited as that media file.
"""
from __future__ import annotations

import csv
import datetime as _dt
import hashlib
import io
import json
import math
import os
import re
import threading
from collections import Counter
from dataclasses import asdict, dataclass, field

DEFAULT_WORKSPACE = os.path.join(os.path.expanduser("~"), ".libra", "workspace")
TEXT_EXT = {".txt", ".md", ".rst", ".text", ".log"}
HTML_EXT = {".html", ".htm", ".xhtml"}
TRANSCRIPT_EXT = {".srt", ".vtt"}
DATA_EXT = {".json", ".jsonl", ".csv", ".tsv"}
MEDIA_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav", ".m4a", ".ogg", ".flac",
             ".jpg", ".jpeg", ".png", ".gif", ".webp", ".heic", ".bmp"}
SUPPORTED = TEXT_EXT | HTML_EXT | TRANSCRIPT_EXT | DATA_EXT
CLARIFY_MARKER = "Your files cover this in more than one place:"

STOPWORDS = set("""a an and are as at be been but by can could did do does for from had has have he her his how i if in
into is it its me my no not of on or our she so than that the their them then there these they this to too us was we
were what when where which who whom why will with would you your yours about please tell show give find any some""".split())


# ------------------------------------------------------------------ text utils
def terms(text: str) -> list[str]:
    """Lower-cased word stems used for search (stop words removed)."""
    out = []
    for w in re.findall(r"[^\W_]+", text.lower()):
        if w in STOPWORDS or (len(w) == 1 and not w.isdigit()):
            continue
        for suffix in ("ingly", "edly", "ing", "ies", "ied", "ed", "es", "s"):
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[: -len(suffix)] + ("y" if suffix in ("ies", "ied") else "")
                break
        out.append(w)
    return out


_CONTINUES = re.compile(r"(it|its|this|that|these|those|they|their|he|she|most of (it|them)|some of (it|them)|all of (it|them)|also|which)\b", re.I)
_SENT = re.compile(r"(?<=[.!?])\s+(?=[\"'(\[A-Z0-9])|\n+")


def sentences(text: str) -> list[str]:
    return [s.strip() for s in _SENT.split(text) if len(s.strip()) > 2]


def _fmt_time(seconds: float) -> str:
    seconds = int(seconds)
    return f"{seconds // 3600:02d}:{seconds % 3600 // 60:02d}:{seconds % 60:02d}"


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ------------------------------------------------------------------ passages
@dataclass
class Passage:
    id: str
    source: str  # path of the file the text came from
    text: str
    start_line: int = 0
    end_line: int = 0
    start_time: float | None = None  # seconds, for transcripts
    end_time: float | None = None
    media: str = ""  # media file this text describes, if any
    kind: str = "file"  # "file" or "correction"
    cues: list = field(default_factory=list)  # [start, end, char offset] per transcript cue
    label: str = ""  # short display name, e.g. "media/notes.md"

    def citation(self, sentence: str | None = None) -> str:
        """Where this passage (or one sentence of it) is: file + lines, or media + timestamps."""
        if self.kind == "correction":
            return f"your correction ({self.source})"
        name = self.label or self.source
        if self.media:
            name = os.path.join(os.path.dirname(name), os.path.basename(self.media))
        via = f" (transcript: {os.path.basename(self.source)})" if self.media else ""
        lo, hi = self.start_line, self.end_line
        t0, t1 = self.start_time, self.end_time
        off = self.text.find(sentence) if sentence else -1
        if off >= 0:
            end = off + len(sentence) - 1
            if self.cues:
                t0 = max((c for c in self.cues if c[2] <= off), key=lambda c: c[2])[0]
                t1 = max((c for c in self.cues if c[2] <= end), key=lambda c: c[2])[1]
            elif lo:
                lo, hi = lo + self.text.count("\n", 0, off), lo + self.text.count("\n", 0, end)
        if t0 is not None:
            return f"{name}{via} @ {_fmt_time(t0)}–{_fmt_time(t1 if t1 is not None else t0)}"
        if lo:
            return f"{name}{via}, " + (f"line {lo}" if lo == hi else f"lines {lo}–{hi}")
        return name + via


def _chunk_lines(lines: list[str], source: str, max_chars: int = 700) -> list[Passage]:
    """Group numbered lines into passages, breaking at blank lines when possible."""
    passages, buf, start = [], [], None
    size = 0

    def flush(end):
        nonlocal buf, start, size
        text = "\n".join(l for l in buf).strip()
        if text:
            passages.append(Passage("", source, text, start, end))
        buf, start, size = [], None, 0

    for i, line in enumerate(lines, 1):
        if not line.strip():
            if size >= max_chars // 2:
                flush(i - 1)
            elif buf:
                buf.append("")
            continue
        if start is None:
            start = i
        buf.append(line.rstrip())
        size += len(line)
        if size >= max_chars:
            flush(i)
    if buf:
        flush(len(lines))
    return passages


_TS = re.compile(r"(?:(\d+):)?(\d{1,2}):(\d{2})[.,](\d{1,3})")


def _seconds(stamp: str) -> float:
    m = _TS.search(stamp)
    if not m:
        raise ValueError(stamp)
    h, mnt, s, frac = m.groups()
    return int(h or 0) * 3600 + int(mnt) * 60 + int(s) + int(frac.ljust(3, "0")) / 1000


def parse_transcript(raw: str) -> list[tuple[float, float, str]]:
    """Cues ``(start, end, text)`` from SRT or WebVTT."""
    cues = []
    for block in re.split(r"\n\s*\n", raw.replace("\r\n", "\n").replace("﻿", "")):
        lines = [l for l in block.strip().split("\n") if l.strip()]
        for i, line in enumerate(lines):
            if "-->" in line:
                a, b = line.split("-->", 1)
                try:
                    start, end = _seconds(a), _seconds(b)
                except ValueError:
                    break
                text = " ".join(lines[i + 1:])
                text = re.sub(r"<[^>]+>", "", text).strip()
                if text:
                    cues.append((start, end, text))
                break
    return cues


def _chunk_cues(cues, source: str, media: str, max_chars: int = 500) -> list[Passage]:
    passages, buf, marks = [], [], []

    def flush():
        text = "\n".join(buf)  # one cue per line, so sentences never straddle cues silently
        passages.append(Passage("", source, text, start_time=marks[0][0], end_time=marks[-1][1],
                                media=media, cues=marks[:]))
        buf.clear()
        marks.clear()

    for s, e, text in cues:
        offset = sum(len(t) + 1 for t in buf)
        buf.append(text)
        marks.append([s, e, offset])
        if offset + len(text) >= max_chars:
            flush()
    if buf:
        flush()
    return passages


def _data_lines(raw: str, ext: str) -> list[str]:
    """Readable lines from JSON/JSONL/CSV, one record per line where possible."""
    if ext in (".csv", ".tsv"):
        reader = csv.DictReader(io.StringIO(raw), delimiter="\t" if ext == ".tsv" else ",")
        return ["; ".join(f"{k}: {v}" for k, v in row.items() if k and v) for row in reader]
    records = []
    try:
        data = json.loads(raw)
        records = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        for line in raw.splitlines():
            if line.strip():
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    lines = []
    for r in records:
        msgs = r.get("messages") or r.get("conversations") if isinstance(r, dict) else None
        if isinstance(msgs, list):
            for m in msgs:
                if isinstance(m, dict):
                    lines.append(f"{m.get('role') or m.get('from')}: {m.get('content') or m.get('value')}")
            lines.append("")
        elif isinstance(r, dict):
            lines.append("; ".join(f"{k}: {v}" for k, v in r.items() if isinstance(v, (str, int, float))))
        else:
            lines.append(str(r))
    return lines


def _media_sibling(path: str) -> str:
    stem = os.path.splitext(path)[0]
    for ext in sorted(MEDIA_EXT):
        for candidate in (stem + ext, stem + ext.upper()):
            if os.path.exists(candidate):
                return candidate
    return ""


def read_passages(path: str) -> list[Passage]:
    ext = os.path.splitext(path)[1].lower()
    with open(path, encoding="utf-8", errors="replace") as f:
        raw = f.read()
    media = _media_sibling(path)
    if ext in TRANSCRIPT_EXT:
        return _chunk_cues(parse_transcript(raw), path, media)
    if ext in HTML_EXT:
        from .web import parse_html

        lines = parse_html(raw).block_text().split("\n")
    elif ext in DATA_EXT:
        lines = _data_lines(raw, ext)
    else:
        lines = raw.replace("\r\n", "\n").split("\n")
    passages = _chunk_lines(lines, path)
    for p in passages:
        p.media = media
    return passages


# ---------------------------------------------------------------------- BM25
class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75):
        self.k1, self.b = k1, b
        self.tf = [Counter(d) for d in docs]
        self.len = [len(d) for d in docs]
        self.avg = (sum(self.len) / len(docs)) if docs else 0.0
        df = Counter(t for d in docs for t in set(d))
        n = len(docs)
        self.idf = {t: math.log(1 + (n - c + 0.5) / (c + 0.5)) for t, c in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        out = []
        for tf, length in zip(self.tf, self.len):
            s = 0.0
            for t in set(query):
                f = tf.get(t)
                if f:
                    s += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * length / (self.avg or 1)))
            out.append(s)
        return out


# -------------------------------------------------------------------- answers
@dataclass
class Answer:
    status: str  # "answered", "clarify" or "not_found"
    text: str
    citations: list[dict] = field(default_factory=list)
    questions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Style:
    bullets: bool = False
    max_sentences: int = 3
    clarify: bool = True


def style_from_rules(rules: list[dict]) -> Style:
    """Turn saved rules into answer settings. Later rules override earlier ones."""
    st = Style()
    for r in rules:
        t = r["text"].lower()
        if re.search(r"\b(bullet|bulleted|list form|as a list)\b", t):
            st.bullets = True
        if re.search(r"\b(paragraph|prose|no bullets)\b", t):
            st.bullets = False
        if re.search(r"\bone sentence\b", t):
            st.max_sentences = 1
        elif re.search(r"\b(short|brief|concise|succinct)\b", t):
            st.max_sentences = 2
        elif re.search(r"\b(detailed|thorough|in depth|long|comprehensive)\b", t):
            st.max_sentences = 6
        if re.search(r"\b(don'?t|do not|never|stop) ask|no clarif|without asking", t):
            st.clarify = False
        if re.search(r"\b(always ask|ask (me )?first|clarify first)\b", t):
            st.clarify = True
    return st


# ------------------------------------------------------------------ workspace
class Workspace:
    def __init__(self, path: str = DEFAULT_WORKSPACE):
        self.path = os.path.expanduser(path)
        os.makedirs(self.path, exist_ok=True)
        self._lock = threading.Lock()
        self.sources: dict = self._read_json("sources.json", {})
        self.rules: list[dict] = self._read_json("rules.json", [])
        self.corrections: list[dict] = self._read_json("corrections.json", [])
        self.passages: list[Passage] = []
        chunks = os.path.join(self.path, "passages.jsonl")
        if os.path.exists(chunks):
            with open(chunks, encoding="utf-8") as f:
                self.passages = [Passage(**json.loads(l)) for l in f if l.strip()]
        self._reindex()

    # ------------------------------------------------------------ storage
    def _read_json(self, name, default):
        p = os.path.join(self.path, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return default

    def _write_json(self, name, value) -> None:
        p = os.path.join(self.path, name)
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            json.dump(value, f, indent=2, ensure_ascii=False)
        os.replace(p + ".tmp", p)

    def _save(self) -> None:
        self._write_json("sources.json", self.sources)
        self._write_json("rules.json", self.rules)
        self._write_json("corrections.json", self.corrections)
        p = os.path.join(self.path, "passages.jsonl")
        with open(p + ".tmp", "w", encoding="utf-8") as f:
            for psg in self.passages:
                f.write(json.dumps(asdict(psg), ensure_ascii=False) + "\n")
        os.replace(p + ".tmp", p)

    def _all_passages(self) -> list[Passage]:
        fixes = [Passage(f"c{i}", c["date"][:10], f"{c['question']} {c['answer']}", kind="correction")
                 for i, c in enumerate(self.corrections)]
        return self.passages + fixes

    def _reindex(self) -> None:
        self._index_passages = self._all_passages()
        self._bm25 = BM25([terms(p.text) for p in self._index_passages])

    # ------------------------------------------------------------- adding
    def add(self, paths, log=print) -> dict:
        """Index files or folders. Re-adding a changed file replaces its old passages."""
        report = {"added": [], "unchanged": [], "unsupported": [], "media_without_text": []}
        files, labels = [], {}
        for path in paths:
            path = os.path.abspath(os.path.expanduser(path))
            if os.path.isdir(path):
                base = os.path.dirname(path.rstrip(os.sep))
                for root, dirs, names in os.walk(path):
                    dirs[:] = sorted(d for d in dirs if not d.startswith("."))
                    for n in sorted(names):
                        if not n.startswith("."):
                            files.append(os.path.join(root, n))
                            labels[files[-1]] = os.path.relpath(files[-1], base)
            elif os.path.exists(path):
                files.append(path)
                labels[path] = os.path.basename(path)
            else:
                raise FileNotFoundError(path)
        with self._lock:
            for file in files:
                ext = os.path.splitext(file)[1].lower()
                if ext in MEDIA_EXT:
                    stem = os.path.splitext(file)[0]
                    if not any(os.path.exists(stem + e) for e in (".srt", ".vtt", ".txt", ".md")):
                        report["media_without_text"].append(file)
                    continue
                if ext not in SUPPORTED:
                    report["unsupported"].append(file)
                    continue
                with open(file, "rb") as f:
                    digest = hashlib.sha1(f.read()).hexdigest()
                if self.sources.get(file, {}).get("sha1") == digest:
                    report["unchanged"].append(file)
                    continue
                new = read_passages(file)
                self.passages = [p for p in self.passages if p.source != file]
                for i, p in enumerate(new):
                    p.id = f"{digest[:10]}-{i}"
                    p.label = labels[file]
                self.passages.extend(new)
                self.sources[file] = {"sha1": digest, "added": _now(), "passages": len(new), "label": labels[file],
                                      "media": new[0].media if new else ""}
                report["added"].append(file)
                if log:
                    log(f"  indexed {file}: {len(new)} passage(s)")
            self._save()
            self._reindex()
        return report

    def remove(self, path: str) -> bool:
        path = os.path.abspath(os.path.expanduser(path))
        with self._lock:
            if path not in self.sources:
                return False
            del self.sources[path]
            self.passages = [p for p in self.passages if p.source != path]
            self._save()
            self._reindex()
        return True

    # ------------------------------------------------------ rules/corrections
    def add_rule(self, text: str) -> dict:
        with self._lock:
            rule = {"text": text.strip(), "date": _now()}
            self.rules.append(rule)
            self._save()
        return rule

    def correct(self, question: str, answer: str) -> dict:
        """Save a correction. It is used for this question from now on, and cited for related ones."""
        with self._lock:
            q = terms(question)
            self.corrections = [c for c in self.corrections if _jaccard(terms(c["question"]), q) < 0.8]
            fix = {"question": question.strip(), "answer": answer.strip(), "date": _now()}
            self.corrections.append(fix)
            self._save()
            self._reindex()
        return fix

    @property
    def style(self) -> Style:
        return style_from_rules(self.rules)

    # ---------------------------------------------------------- answering
    def search(self, question: str, k: int = 5, only_source: str | None = None):
        q = terms(question)
        if not q or not self._index_passages:
            return []
        scored = [(s, p) for s, p in zip(self._bm25.scores(q), self._index_passages)
                  if s > 0 and (only_source is None or p.source == only_source or p.media == only_source)]
        scored.sort(key=lambda x: -x[0])
        return scored[:k]

    def _evidence(self, question: str, hits) -> list:
        """``(score, sentence, passage)`` for sentences that carry the question's key words, best first.

        Headings and fragments are skipped, and so are sentences that only share the
        common words (score below half of the best sentence's)."""
        q = set(terms(question))
        idf = self._bm25.idf
        found = []
        for rank, (_, p) in enumerate(hits):
            for sent in sentences(p.text):
                if sent.lstrip().startswith("#") or len(sent.split()) < 4:
                    continue
                score = sum(idf.get(t, 0) for t in q & set(terms(sent)))
                if score > 0:
                    found.append((score * (1 - 0.05 * rank), sent, p))
        found.sort(key=lambda x: -x[0])
        if found:
            best = found[0][0]
            found = [f for f in found if f[0] >= 0.5 * best]
        return found

    def _ambiguous(self, question: str, hits) -> list:
        """Best sentence per file when two or more files answer about equally well, else []."""
        by_source = {}
        for score, sent, p in self._evidence(question, hits):
            key = p.media or p.source
            if p.kind == "file" and key not in by_source:
                by_source[key] = (score, sent, p)
        ranked = sorted(by_source.values(), key=lambda x: -x[0])
        if len(ranked) >= 2 and ranked[1][0] >= 0.85 * ranked[0][0]:
            return ranked[:3]
        return []

    def ask(self, question: str, clarify: bool | None = None, previous_question: str | None = None) -> Answer:
        """Answer from your files. ``previous_question`` is set when ``question`` replies to a clarification."""
        style = self.style
        clarify = style.clarify if clarify is None else clarify

        if previous_question:
            options = [p.media or p.source for _, _, p in self._ambiguous(previous_question, self.search(previous_question, 8))]
            if options:
                if re.fullmatch(r"\W*(all|both|any|either|all of them|everything)\W*", question.lower()):
                    return self._compose(previous_question, self.search(previous_question, 8), style)
                chosen = _choose(question, options)
                if chosen:
                    return self._compose(previous_question, self.search(previous_question, 8, chosen), style)

        q = terms(question)
        for c in reversed(self.corrections):  # an answer you corrected wins
            if q and _jaccard(terms(c["question"]), q) >= 0.6:
                cite = {"n": 1, "source": f"your correction ({c['date'][:10]})", "file": "", "quote": c["answer"]}
                return Answer("answered", self._format([(c["answer"], 1)], [cite], style), [cite])

        hits = self.search(question, 8)
        if not hits:
            n = len(self.sources)
            return Answer("not_found",
                          f"I couldn't find anything about that in your files ({n} file{'s' * (n != 1)} searched), "
                          "so I won't guess.\n\nWhich file or recording covers it? You can add it with /add <path>.",
                          questions=["Which file or recording covers this?"])

        ranked = self._ambiguous(question, hits) if clarify else []
        if ranked:
            lines = [CLARIFY_MARKER]
            for i, (_, sent, p) in enumerate(ranked, 1):
                lines.append(f"{i}. {p.citation(sent)}: \"{sent}\"")
            ask = "Which one do you mean? Reply with a number or file name, or say \"all\"."
            return Answer("clarify", "\n".join(lines) + "\n\n" + ask, questions=[ask])

        return self._compose(question, hits, style)

    def ask_chat(self, messages: list[dict], clarify: bool | None = None) -> Answer:
        """Answer the last user message of a conversation, following up a clarification if there was one."""
        user = [i for i, m in enumerate(messages) if m.get("role") == "user"]
        if not user:
            raise ValueError("no user message")
        last = user[-1]
        previous = None
        if (last >= 2 and messages[last - 1].get("role") == "assistant"
                and str(messages[last - 1].get("content", "")).startswith(CLARIFY_MARKER)
                and messages[last - 2].get("role") == "user"):
            previous = str(messages[last - 2].get("content", ""))
        return self.ask(str(messages[last].get("content", "")), clarify, previous)

    def _compose(self, question: str, hits, style: Style) -> Answer:
        candidates = self._evidence(question, hits[:4])
        if not candidates:
            return Answer("not_found", "Your files mention some of these words, but no passage answers this "
                                       "directly, so I won't guess. Which file or recording should I look in?",
                          questions=["Which file or recording should I look in?"])
        # A following sentence that continues the thought ("It ...", "Most of it ...") comes along.
        expanded = []
        for score, sent, p in candidates:
            expanded.append((score, sent, p))
            sents = sentences(p.text)
            if sent in sents:
                i = sents.index(sent)
                if i + 1 < len(sents) and _CONTINUES.match(sents[i + 1]):
                    expanded.append((score, sents[i + 1], p))
        candidates = expanded
        chosen, seen_text, cites, order = [], set(), [], {}
        for _, sent, p in candidates:
            norm = sent.lower()
            if norm in seen_text:
                continue
            seen_text.add(norm)
            where = p.citation(sent)
            if where not in order:
                order[where] = len(order) + 1
                cites.append({"n": order[where], "source": where, "file": p.media or p.source,
                              "quote": sent if len(sent) <= 200 else sent[:199] + "…"})
            chosen.append((sent, order[where]))
            if len(chosen) >= style.max_sentences:
                break
        return Answer("answered", self._format(chosen, cites, style), cites)

    @staticmethod
    def _format(chosen, cites, style: Style) -> str:
        if style.bullets:
            body = "\n".join(f"- {s} [{n}]" for s, n in chosen)
        else:
            body = " ".join(f"{s} [{n}]" for s, n in chosen)
        sources = "\n".join(f"[{c['n']}] {c['source']}" for c in cites)
        return f"{body}\n\nSources:\n{sources}"

    # ------------------------------------------------------------ training
    def export_training(self, out_dir: str) -> dict:
        """Write your files' text and your corrections as data for ``libra train``."""
        os.makedirs(out_dir, exist_ok=True)
        docs = {}
        for p in self.passages:
            docs.setdefault(p.source, []).append(p.text)
        with open(os.path.join(out_dir, "text.jsonl"), "w", encoding="utf-8") as f:
            for text in docs.values():
                f.write(json.dumps({"text": "\n\n".join(text)}, ensure_ascii=False) + "\n")
        system = " ".join(r["text"] for r in self.rules)
        with open(os.path.join(out_dir, "chat.jsonl"), "w", encoding="utf-8") as f:
            for c in self.corrections:
                msgs = ([{"role": "system", "content": system}] if system else []) + [
                    {"role": "user", "content": c["question"]}, {"role": "assistant", "content": c["answer"]}]
                f.write(json.dumps({"messages": msgs}, ensure_ascii=False) + "\n")
        return {"documents": len(docs), "conversations": len(self.corrections), "out": out_dir}

    def summary(self) -> str:
        lines = [f"workspace: {self.path}",
                 f"files: {len(self.sources)}, passages: {len(self.passages)}, "
                 f"corrections: {len(self.corrections)}, rules: {len(self.rules)}"]
        for path, info in sorted(self.sources.items()):
            media = f" (describes {os.path.basename(info['media'])})" if info.get("media") else ""
            lines.append(f"  {info.get('label', path)}: {info['passages']} passage(s){media}  [{path}]")
        return "\n".join(lines)


def _jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a and b else 0.0


def _snippet(text: str, q, width: int = 120) -> str:
    q = set(q)
    best = max(sentences(text) or [text], key=lambda s: len(q & set(terms(s))))
    best = re.sub(r"\s+", " ", best)
    return best if len(best) <= width else best[: width - 1].rstrip() + "…"


def _choose(reply: str, options: list[str]):
    """Which option a clarification reply picks: a number, 'first'/'second', or a file name."""
    r = reply.strip().lower()
    words = {"first": 1, "second": 2, "third": 3, "1st": 1, "2nd": 2, "3rd": 3, "one": 1, "two": 2, "three": 3}
    m = re.fullmatch(r"\D*(\d)\D*", r)
    idx = int(m.group(1)) if m else next((n for w, n in words.items() if re.search(rf"\b{w}\b", r)), None)
    if idx and 1 <= idx <= len(options):
        return options[idx - 1]
    for opt in options:
        base = os.path.basename(opt).lower()
        if base in r or os.path.splitext(base)[0] in r:
            return opt
    return None
