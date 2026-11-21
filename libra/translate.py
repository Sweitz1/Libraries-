"""Translation between any languages you have data for, built from scratch.

Engines, tried in order for each sentence (the result always says which one
answered, and how confident it is):

1. **Translation memory, exact**: the sentence (ignoring case and
   punctuation) is in your imported sentence pairs.
2. **Adapted match**: a very similar sentence is in memory and the
   differing words can be swapped using the bilingual dictionary
   ("I have a dog" -> "I have a cat").
3. **Neural model**: a Libra model you trained on your pairs
   (``prepare`` writes the training data).
4. **Word-by-word gloss**: dictionary lookups in the source word order.
   Rough, and labelled as such.

If a language pair has no data at all, it can go through English ("pivot")
when both halves exist. Otherwise it says there is no data instead of guessing.

The dictionary comes from your own word lists and/or **IBM Model 1** word
alignment learned from your sentence pairs (expectation-maximisation).

Data lives in ``~/.libra/translate.db`` (SQLite with an inverted index).
"""
from __future__ import annotations

import csv
import difflib
import io
import json
import math
import os
import re
import sqlite3
import threading
import xml.etree.ElementTree as ET
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from . import home
from .lang import build_profile, code_for, detect, name

K1, B = 1.2, 0.75
GENDERED = {"es", "fr", "it", "pt", "de", "ca", "ro", "ru", "uk", "pl", "cs", "sk", "ar", "he", "hi", "el", "nl", "sv",
            "da", "nb", "bg", "sr", "hr", "lt", "lv", "gl"}
_CJK = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯฀-๿]")


# ------------------------------------------------------------------ text
def norm(text: str) -> str:
    """Comparison form: lower case, punctuation removed, single spaces."""
    return " ".join(re.findall(r"[^\W_]+", text.lower()))


def words(text: str) -> list[str]:
    """Search tokens. Scripts written without spaces are split into characters and character pairs."""
    out = []
    for w in re.findall(r"[^\W_]+", text.lower()):
        if _CJK.search(w):
            out.extend(w)
            out.extend(w[i : i + 2] for i in range(len(w) - 1))
        else:
            out.append(w)
    return out


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?。！？])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _tokens_keep(text: str) -> list[str]:
    """Words and punctuation, for glossing (keeps punctuation in place)."""
    return re.findall(r"[^\W_]+(?:['’][^\W_]+)?|[^\w\s]", text)


# ---------------------------------------------------------------- readers
def read_pairs(path: str, src: str, tgt: str, second: str | None = None):
    """Yield ``(source sentence, target sentence)`` from many common formats.

    * ``.tsv`` / tab-separated ``.txt``: ``src<TAB>tgt``, or the Tatoeba export
      ``id<TAB>src<TAB>id<TAB>tgt``
    * two aligned plain-text files (OPUS "Moses" format): ``path`` + ``second``
    * ``.tmx`` translation memory
    * ``.jsonl`` with ``{"src", "tgt"}``, ``{"translation": {"en": ..., "es": ...}}`` or ``{"en": ..., "es": ...}``
    * ``.csv`` whose headers are the two language codes or names
    """
    if second:
        with open(path, encoding="utf-8", errors="replace") as fa, open(second, encoding="utf-8", errors="replace") as fb:
            for a, b in zip(fa, fb):
                if a.strip() and b.strip():
                    yield a.strip(), b.strip()
        return
    ext = os.path.splitext(path)[1].lower()
    if ext == ".tmx":
        yield from _read_tmx(path, src, tgt)
        return
    with open(path, encoding="utf-8", errors="replace") as f:
        if ext == ".jsonl":
            for line in f:
                if not line.strip():
                    continue
                r = json.loads(line)
                r = r.get("translation", r)
                a = r.get("src") or r.get("source") or _by_lang(r, src)
                b = r.get("tgt") or r.get("target") or _by_lang(r, tgt)
                if a and b:
                    yield str(a).strip(), str(b).strip()
            return
        if ext == ".csv":
            reader = csv.DictReader(f)
            cols = {code_for(c or "") or (c or "").lower(): c for c in reader.fieldnames or []}
            if src not in cols or tgt not in cols:
                raise ValueError(f"{path}: CSV needs columns for {src} and {tgt}; found {reader.fieldnames}")
            for row in reader:
                if row[cols[src]] and row[cols[tgt]]:
                    yield row[cols[src]].strip(), row[cols[tgt]].strip()
            return
        for line in f:
            cols = line.rstrip("\n").split("\t")
            if len(cols) >= 4 and cols[0].isdigit() and cols[2].isdigit():
                a, b = cols[1], cols[3]
            elif len(cols) >= 2:
                a, b = cols[0], cols[1]
            else:
                continue
            if a.strip() and b.strip():
                yield a.strip(), b.strip()


def _by_lang(record: dict, code: str):
    for k, v in record.items():
        if code_for(str(k)) == code:
            return v
    return None


def _read_tmx(path: str, src: str, tgt: str):
    lang_attr = "{http://www.w3.org/XML/1998/namespace}lang"
    for _, el in ET.iterparse(path, events=("end",)):
        if el.tag.endswith("tu"):
            segs = {}
            for tuv in el:
                if tuv.tag.endswith("tuv"):
                    code = code_for(tuv.get(lang_attr) or tuv.get("lang") or "")
                    seg = next((s for s in tuv if s.tag.endswith("seg")), None)
                    if code and seg is not None:
                        segs[code] = "".join(seg.itertext()).strip()
            if segs.get(src) and segs.get(tgt):
                yield segs[src], segs[tgt]
            el.clear()


# ------------------------------------------------------------ IBM Model 1
def train_ibm1(pairs, iterations: int = 6, log=None) -> dict:
    """Word translation probabilities t[source word][target word] by EM (Brown et al., 1993)."""
    pairs = [([w for w in words(a) if len(w) < 40], [w for w in words(b) if len(w) < 40]) for a, b in pairs]
    pairs = [(a, b) for a, b in pairs if a and b and len(a) <= 60 and len(b) <= 60]
    t: dict = defaultdict(dict)
    for a, b in pairs:
        for e in set(a) | {None}:
            row = t[e]
            for f in b:
                row[f] = 1.0
    for e, row in t.items():  # uniform start
        u = 1.0 / len(row)
        for f in row:
            row[f] = u
    for it in range(iterations):
        count: dict = defaultdict(lambda: defaultdict(float))
        total: dict = defaultdict(float)
        for a, b in pairs:
            src = a + [None]
            for f in b:
                probs = [t[e].get(f, 0.0) for e in src]
                z = sum(probs)
                if z <= 0:
                    continue
                for e, pe in zip(src, probs):
                    if pe:
                        c = pe / z
                        count[e][f] += c
                        total[e] += c
        for e, row in count.items():
            tot = total[e]
            t[e] = {f: c / tot for f, c in row.items() if c / tot > 1e-4}
        if log:
            log(f"  IBM Model 1 iteration {it + 1}/{iterations}")
    t.pop(None, None)
    return t


# ---------------------------------------------------------------- results
@dataclass
class Translation:
    text: str
    source_lang: str
    target_lang: str
    method: str  # exact, adapted, neural, similar, gloss, pivot, none
    confidence: float
    notes: list[str] = field(default_factory=list)
    parts: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def describe(self) -> str:
        labels = {"exact": "translation memory, exact match", "adapted": "adapted from a similar sentence in memory",
                  "neural": "neural model trained on your pairs (check it)", "similar": "closest sentence in memory",
                  "gloss": "word-by-word gloss (rough; source word order)", "pivot": "via English",
                  "mixed": "several methods (see parts)", "none": "no translation available"}
        head = f"{name(self.source_lang)} → {name(self.target_lang)} · {labels.get(self.method, self.method)}"
        if self.method != "none":
            head += f" · confidence {round(self.confidence * 100)}%"
        return "\n".join([self.text, "", f"[{head}]"] + [f"note: {n}" for n in self.notes])


# --------------------------------------------------------------- the store
class Translator:
    def __init__(self, db_path: str | None = None):
        self.path = db_path or home.path("translate.db")
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        self._profiles = None
        with self._lock, self._db:
            self._db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS pairs(id INTEGER PRIMARY KEY, la TEXT, lb TEXT, a TEXT, b TEXT,
                    na TEXT, nb TEXT, alen INTEGER, blen INTEGER, source TEXT, UNIQUE(la, lb, na, nb));
                CREATE INDEX IF NOT EXISTS pairs_a ON pairs(la, na);
                CREATE INDEX IF NOT EXISTS pairs_b ON pairs(lb, nb);
                CREATE TABLE IF NOT EXISTS postings(term TEXT, pair INTEGER, side INTEGER, tf INTEGER,
                    PRIMARY KEY(term, pair, side)) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS terms(term TEXT PRIMARY KEY, df INTEGER) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS stats(key TEXT PRIMARY KEY, value REAL);
                CREATE TABLE IF NOT EXISTS dictionary(src TEXT, tgt TEXT, sw TEXT, tw TEXT, p REAL, origin TEXT,
                    PRIMARY KEY(src, tgt, sw, tw)) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS profiles(lang TEXT PRIMARY KEY, grams TEXT);
                CREATE TABLE IF NOT EXISTS models(src TEXT, tgt TEXT, path TEXT, PRIMARY KEY(src, tgt));
            """)

    def __del__(self):
        try:
            self._db.close()
        except Exception:
            pass

    # ------------------------------------------------------------- import
    def add_pairs(self, pairs, src: str, tgt: str, source: str = "", log=None) -> int:
        """Store sentence pairs (deduplicated) and index both sides. Returns how many were new."""
        added, samples = 0, {src: [], tgt: []}
        with self._lock, self._db:
            db = self._db
            df: Counter = Counter()
            lens: Counter = Counter()
            counts: Counter = Counter()
            for a, b in pairs:
                na, nb = norm(a), norm(b)
                if not na or not nb:
                    continue
                wa, wb = words(a), words(b)
                cur = db.execute("INSERT OR IGNORE INTO pairs(la, lb, a, b, na, nb, alen, blen, source) "
                                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", (src, tgt, a, b, na, nb, len(wa), len(wb), source))
                if not cur.rowcount:
                    continue
                pid = cur.lastrowid
                rows = []
                for side, lang, ws in ((0, src, wa), (1, tgt, wb)):
                    for w, c in Counter(ws).items():
                        term = f"{lang}|{w}"
                        rows.append((term, pid, side, c))
                        df[term] += 1
                    lens[lang] += len(ws)
                    counts[lang] += 1
                db.executemany("INSERT INTO postings VALUES (?, ?, ?, ?)", rows)
                added += 1
                if len(samples[src]) < 3000:
                    samples[src].append(a)
                    samples[tgt].append(b)
                if log and added % 50000 == 0:
                    log(f"  {added:,} pairs…")
            db.executemany("INSERT INTO terms VALUES (?, ?) ON CONFLICT(term) DO UPDATE SET df = df + excluded.df",
                           list(df.items()))
            for lang in counts:
                for key, val in ((f"n:{lang}", counts[lang]), (f"len:{lang}", lens[lang])):
                    db.execute("INSERT INTO stats VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = value + ?",
                               (key, val, val))
            for lang, texts in samples.items():  # letter profiles make your languages detectable
                if len(texts) >= 20:
                    db.execute("INSERT OR REPLACE INTO profiles VALUES (?, ?)", (lang, json.dumps(build_profile(texts))))
        self._profiles = None
        return added

    def add_dictionary(self, path: str, src: str, tgt: str) -> int:
        """Word list: ``word<TAB>translation`` (alternatives separated by ``;`` or ``,``)."""
        rows = []
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                if "\t" not in line or line.startswith("#"):
                    continue
                sw, tws = line.rstrip("\n").split("\t", 1)
                alts = [t.strip() for t in re.split(r"[;,]", tws) if t.strip()]
                for i, tw in enumerate(alts):
                    rows.append((src, tgt, norm(sw), tw, 1.0 - 0.1 * i, "user"))
        with self._lock, self._db:
            self._db.executemany("INSERT OR REPLACE INTO dictionary VALUES (?, ?, ?, ?, ?, ?)", rows)
        return len(rows)

    def align(self, src: str, tgt: str, max_pairs: int = 50000, iterations: int = 6, log=None) -> int:
        """Learn a dictionary from stored pairs with IBM Model 1. Returns entries written."""
        with self._lock:
            rows = self._db.execute(
                "SELECT a, b FROM pairs WHERE la = ? AND lb = ? UNION ALL SELECT b, a FROM pairs WHERE la = ? AND lb = ? "
                "LIMIT ?", (src, tgt, tgt, src, max_pairs)).fetchall()
        if not rows:
            raise ValueError(f"no {src}-{tgt} sentence pairs; import some with: libra translate add FILE --src {src} --tgt {tgt}")
        # Align both directions and keep only pairs both agree on ("intersection" symmetrization).
        # This removes one-sided matches such as English "I" -> Spanish "un" (Spanish often drops "yo").
        t = train_ibm1(rows, iterations, log)
        t_rev = train_ibm1([(b, a) for a, b in rows], iterations, log)
        freq = Counter(w for a, _ in rows for w in set(words(a)))

        def top(row, k=2):
            return [w for w, p in sorted(row.items(), key=lambda x: -x[1])[:k] if p >= 0.1]

        entries = []
        for e, row in t.items():
            if freq[e] < 2:
                continue
            for f in top(row, 3):
                if e in top(t_rev.get(f, {})):
                    entries.append((src, tgt, e, f, round(row[f], 4), "ibm1"))
        with self._lock, self._db:
            self._db.execute("DELETE FROM dictionary WHERE src = ? AND tgt = ? AND origin = 'ibm1'", (src, tgt))
            self._db.executemany("INSERT OR IGNORE INTO dictionary VALUES (?, ?, ?, ?, ?, ?)", entries)
        return len(entries)

    def set_model(self, src: str, tgt: str, path: str | None) -> None:
        with self._lock, self._db:
            if path:
                self._db.execute("INSERT OR REPLACE INTO models VALUES (?, ?, ?)", (src, tgt, os.path.abspath(path)))
            else:
                self._db.execute("DELETE FROM models WHERE src = ? AND tgt = ?", (src, tgt))

    def export_training(self, out_dir: str, src: str, tgt: str, both: bool = False, max_pairs: int = 1_000_000) -> int:
        """Chat-formatted training data for a translation model (``libra train OUT``)."""
        os.makedirs(out_dir, exist_ok=True)
        n = 0
        with self._lock:
            rows = self._db.execute("SELECT a, b FROM pairs WHERE la = ? AND lb = ? UNION ALL "
                                    "SELECT b, a FROM pairs WHERE la = ? AND lb = ? LIMIT ?",
                                    (src, tgt, tgt, src, max_pairs)).fetchall()
        with open(os.path.join(out_dir, f"translate_{src}_{tgt}.jsonl"), "w", encoding="utf-8") as f:
            for a, b in rows:
                for s, t, x, y in ((src, tgt, a, b), (tgt, src, b, a))[: 2 if both else 1]:
                    msgs = [{"role": "user", "content": _prompt(s, t, x)}, {"role": "assistant", "content": y}]
                    f.write(json.dumps({"messages": msgs}, ensure_ascii=False) + "\n")
                    n += 1
        return n

    # ------------------------------------------------------------- lookup
    def pairs_for(self, src: str, tgt: str) -> int:
        with self._lock:
            return self._db.execute("SELECT COUNT(*) FROM pairs WHERE (la = ? AND lb = ?) OR (la = ? AND lb = ?)",
                                    (src, tgt, tgt, src)).fetchone()[0]

    def has_data(self, src: str, tgt: str) -> bool:
        with self._lock:
            d = self._db.execute("SELECT 1 FROM dictionary WHERE src = ? AND tgt = ? LIMIT 1", (src, tgt)).fetchone()
            m = self._db.execute("SELECT 1 FROM models WHERE src = ? AND tgt = ?", (src, tgt)).fetchone()
        return bool(d or m) or self.pairs_for(src, tgt) > 0

    def stats(self) -> dict:
        with self._lock:
            db = self._db
            pairs = db.execute("SELECT la, lb, COUNT(*) FROM pairs GROUP BY la, lb ORDER BY 3 DESC").fetchall()
            dicts = db.execute("SELECT src, tgt, origin, COUNT(*) FROM dictionary GROUP BY src, tgt, origin").fetchall()
            models = db.execute("SELECT src, tgt, path FROM models").fetchall()
        return {"pairs": pairs, "dictionary": dicts, "models": models}

    def profiles(self) -> dict:
        if self._profiles is None:
            with self._lock:
                self._profiles = {l: json.loads(g) for l, g in self._db.execute("SELECT lang, grams FROM profiles")}
        return self._profiles

    def detect(self, text: str) -> list[tuple[str, float]]:
        return detect(text, self.profiles())

    def exact(self, text: str, src: str, tgt: str) -> str | None:
        n = norm(text)
        with self._lock:
            row = self._db.execute("SELECT b FROM pairs WHERE la = ? AND lb = ? AND na = ? LIMIT 1", (src, tgt, n)).fetchone() \
                or self._db.execute("SELECT a FROM pairs WHERE lb = ? AND la = ? AND nb = ? LIMIT 1", (src, tgt, n)).fetchone()
        return row[0] if row else None

    def similar(self, text: str, src: str, tgt: str, limit: int = 20) -> list[tuple[float, str, str]]:
        """``(similarity 0..1, stored source sentence, its translation)``, best first."""
        q = sorted(set(words(text)))
        if not q:
            return []
        with self._lock:
            db = self._db
            st = dict(db.execute("SELECT key, value FROM stats WHERE key IN (?, ?)", (f"n:{src}", f"len:{src}")))
            n, total = st.get(f"n:{src}", 0), st.get(f"len:{src}", 0)
            if not n:
                return []
            avg = total / n
            terms_ = [f"{src}|{w}" for w in q]
            marks = ",".join("?" * len(terms_))
            dfs = dict(db.execute(f"SELECT term, df FROM terms WHERE term IN ({marks})", terms_).fetchall())
            if not dfs:
                return []
            weights = [(t, math.log(1 + (n - df + 0.5) / (df + 0.5))) for t, df in dfs.items()]
            case = " ".join("WHEN ? THEN ?" for _ in weights)
            sql = (f"SELECT s.a, s.b, p.side, SUM((CASE p.term {case} END) * p.tf * {K1 + 1} / (p.tf + {K1} * "
                   f"(1 - {B} + {B} * (CASE p.side WHEN 0 THEN s.alen ELSE s.blen END) / ?))) AS score "
                   f"FROM postings p JOIN pairs s ON s.id = p.pair WHERE p.term IN ({','.join('?' * len(weights))}) "
                   f"AND ((p.side = 0 AND s.la = ? AND s.lb = ?) OR (p.side = 1 AND s.lb = ? AND s.la = ?)) "
                   f"GROUP BY p.pair, p.side ORDER BY score DESC LIMIT ?")
            params = [x for tw in weights for x in tw] + [avg] + [t for t, _ in weights] + [src, tgt, src, tgt, limit]
            rows = db.execute(sql, params).fetchall()
        qn = norm(text).split()
        out = []
        for a, b, side, _ in rows:
            stored, translated = (a, b) if side == 0 else (b, a)
            sim = difflib.SequenceMatcher(None, qn, norm(stored).split()).ratio()
            out.append((sim, stored, translated))
        out.sort(key=lambda x: -x[0])
        return out

    def lookup(self, word: str, src: str, tgt: str) -> list[str]:
        """Dictionary translations for a word or phrase, best first (your lists before learned ones)."""
        with self._lock:
            rows = self._db.execute("SELECT tw FROM dictionary WHERE src = ? AND tgt = ? AND sw = ? "
                                    "ORDER BY origin = 'user' DESC, p DESC LIMIT 5", (src, tgt, norm(word))).fetchall()
            if not rows:  # a list for the other direction works too
                rows = self._db.execute("SELECT sw FROM dictionary WHERE src = ? AND tgt = ? AND lower(tw) = ? "
                                        "ORDER BY origin = 'user' DESC, p DESC LIMIT 5",
                                        (tgt, src, norm(word))).fetchall()
        return [r[0] for r in rows]

    def _model(self, src: str, tgt: str):
        with self._lock:
            row = self._db.execute("SELECT path FROM models WHERE src = ? AND tgt = ?", (src, tgt)).fetchone()
        return row[0] if row else None

    # ---------------------------------------------------------- translate
    def adapt(self, text: str, stored: str, translated: str, src: str, tgt: str, keep_unknown: bool = False):
        """Swap the words that differ between ``text`` and ``stored`` inside ``translated``.

        Returns ``(text, unknown words)`` or None. With ``keep_unknown``, a word the dictionary
        doesn't know is put in as ``[word]`` instead of giving up."""
        q, s = norm(text).split(), norm(stored).split()
        out, unknown = translated, []
        for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, s, q).get_opcodes():
            if op == "equal":
                continue
            if op != "replace" or i2 - i1 != 1 or j2 - j1 != 1:
                return None  # only single-word substitutions are safe to repair
            old = self.lookup(s[i1], src, tgt)
            new = self.lookup(q[j1], src, tgt)
            if not new and keep_unknown:
                new = [f"[{q[j1]}]"]
                unknown.append(q[j1])
            if not old or not new:
                return None
            for cand in old:
                pattern = re.compile(rf"(?<![^\W_]){re.escape(cand)}(?![^\W_])", re.I)
                if pattern.search(out):
                    out = pattern.sub(lambda m: _match_case(new[0], m.group()), out, count=1)
                    break
            else:
                return None
        return out, unknown

    def gloss(self, text: str, src: str, tgt: str) -> tuple[str, float, list[str]]:
        toks = _tokens_keep(text)
        out, unknown, known, total, i = [], [], 0, 0, 0
        while i < len(toks):
            tok = toks[i]
            if not re.match(r"[^\W_]", tok):
                out.append(tok)
                i += 1
                continue
            done = False
            for n in (3, 2):  # longest phrase first
                chunk = [t for t in toks[i : i + n] if re.match(r"[^\W_]", t)]
                if len(chunk) == n and len(toks[i : i + n]) == n:
                    hit = self.lookup(" ".join(chunk), src, tgt)
                    if hit:
                        out.append(hit[0])
                        known += n
                        total += n
                        i += n
                        done = True
                        break
            if done:
                continue
            total += 1
            hit = self.lookup(tok, src, tgt)
            if hit:
                out.append(_match_case(hit[0], tok))
                known += 1
            else:
                out.append(tok)
                unknown.append(tok)
            i += 1
        text_out = re.sub(r"\s+([,.!?;:)\]。！？])", r"\1", " ".join(out))
        text_out = re.sub(r"([(¿¡\[])\s+", r"\1", text_out)
        first = next((t for t in toks if re.match(r"[^\W_]", t)), "")
        if first[:1].isupper():  # keep the sentence capital
            m = re.search(r"[^\W\d_]", text_out)
            if m:
                text_out = text_out[: m.start()] + text_out[m.start()].upper() + text_out[m.start() + 1:]
        return text_out, (known / total if total else 0.0), unknown

    def _neural(self, text: str, src: str, tgt: str) -> str | None:
        path = self._model(src, tgt)
        if not path:
            return None
        from . import checkpoint
        from .chat import STOP_TOKENS, render
        from .generate import SamplingParams, generate

        cache = getattr(self, "_models", {})
        self._models = cache
        if path not in cache:
            cache[path] = checkpoint.load(path)[:2]
        model, tok = cache[path]
        prompt = render([{"role": "user", "content": _prompt(src, tgt, text)}], add_generation_prompt=True)
        out = generate(model, tok, prompt, SamplingParams(max_tokens=min(400, 4 * len(text.split()) + 24),
                                                          temperature=0, repetition_penalty=1.1),
                       stop_tokens=STOP_TOKENS).strip()
        return out or None

    def sentence(self, text: str, src: str, tgt: str) -> tuple[str, str, float, list[str]]:
        """``(translation, method, confidence, notes)`` for one sentence."""
        hit = self.exact(text, src, tgt)
        if hit:
            return _carry_punct(text, hit), "exact", 1.0, []
        similar = self.similar(text, src, tgt, 10)
        gendered = tgt in GENDERED
        agree = [f"check {name(tgt)} gender/number agreement (articles, adjectives) after the word swap"] if gendered else []
        for sim, stored, translated in similar:
            if sim >= 0.85:
                got = self.adapt(text, stored, translated, src, tgt)
                if got:
                    return got[0], "adapted", round(sim * 0.9, 3), [f'adapted from "{stored}" → "{translated}"'] + agree
        neural = self._neural(text, src, tgt)
        if neural:
            notes = [f'closest example in memory: "{similar[0][1]}" → "{similar[0][2]}"'] if similar else []
            return neural, "neural", 0.5, notes
        for keep in (False, True):
            for sim, stored, translated in similar:
                if sim >= 0.6:
                    got = self.adapt(text, stored, translated, src, tgt, keep_unknown=keep)
                    if got:
                        out, unknown = got
                        notes = [f'adapted from "{stored}" → "{translated}"'] + agree
                        if unknown:
                            notes.append(f"left untranslated (not in the dictionary): {', '.join(unknown)}")
                        conf = sim * (0.8 if not unknown else 0.45)
                        return out, "adapted", round(conf, 3), notes
        glossed, coverage, unknown = self.gloss(text, src, tgt)
        if coverage >= 0.5:
            notes = [f"not in the dictionary: {', '.join(unknown)}"] if unknown else []
            if similar and similar[0][0] >= 0.5:
                notes.append(f'similar sentence in memory: "{similar[0][1]}" → "{similar[0][2]}"')
            return glossed, "gloss", round(0.35 * coverage, 3), notes
        if similar and similar[0][0] >= 0.5:
            sim, stored, translated = similar[0]
            return translated, "similar", round(sim * 0.5, 3), [
                f'no exact match; this is the translation of a similar sentence: "{stored}"']
        return text, "none", 0.0, [f"no translation found for this sentence ({name(src)} → {name(tgt)})"]

    def translate(self, text: str, target: str, source: str = "auto") -> Translation:
        tgt = code_for(target)
        if not tgt:
            raise ValueError(f"unknown target language {target!r}")
        notes = []
        if source in ("", "auto", None):
            guesses = self.detect(text)
            if not guesses:
                raise ValueError("couldn't tell what language the text is in; pass the source language")
            src = guesses[0][0]
            if guesses[0][1] < 0.5:  # unsure: prefer a likely language we actually have data for
                for code, _ in guesses[:4]:
                    if self.has_data(code, tgt):
                        src = code
                        break
            sure = dict(guesses).get(src, 0)
            notes.append(f"detected {name(src)} ({round(sure * 100)}% sure)")
            if sure < 0.5:
                others = ", ".join(name(c) for c, _ in guesses[1:3] if c != src)
                notes.append(f"language detection is unsure{f' (maybe {others})' if others else ''}; "
                             "give the source language to be certain")
        else:
            src = code_for(source)
            if not src:
                raise ValueError(f"unknown source language {source!r}")
        if src == tgt:
            return Translation(text, src, tgt, "exact", 1.0, notes + ["source and target language are the same"])
        if not self.has_data(src, tgt):
            if src != "en" and tgt != "en" and self.has_data(src, "en") and self.has_data("en", tgt):
                first = self.translate(text, "en", src)
                second = self.translate(first.text, tgt, "en")
                return Translation(second.text, src, tgt, "pivot", round(first.confidence * second.confidence, 3),
                                   notes + [f"via English: {first.text}"], first.parts + second.parts)
            return Translation(text, src, tgt, "none", 0.0, notes + [
                f"no {name(src)}–{name(tgt)} data yet. Import sentence pairs with: "
                f"libra translate add FILE --src {src} --tgt {tgt}"])
        parts, outs = [], []
        for sent in split_sentences(text):
            out, method, conf, sent_notes = self.sentence(sent, src, tgt)
            parts.append({"source": sent, "text": out, "method": method, "confidence": conf, "notes": sent_notes})
            outs.append(out)
        methods = {p["method"] for p in parts}
        method = methods.pop() if len(methods) == 1 else "mixed"
        conf = round(min(p["confidence"] for p in parts), 3) if parts else 0.0
        for p in parts:
            notes.extend(p["notes"])
        return Translation(" ".join(outs), src, tgt, method, conf, notes, parts)


def _prompt(src: str, tgt: str, text: str) -> str:
    return f"Translate from {name(src)} to {name(tgt)}: {text}"


def _match_case(word: str, like: str) -> str:
    if like[:1].isupper() and not like.isupper():
        return word[:1].upper() + word[1:]
    if like.isupper() and len(like) > 1:
        return word.upper()
    return word


def _carry_punct(source: str, translated: str) -> str:
    """Keep the source's final ?/!/. if the stored translation dropped it."""
    end = source.rstrip()[-1:] if source.rstrip() else ""
    if end in "?!." and translated and translated.rstrip()[-1:] not in "?!.。！？":
        return translated.rstrip() + end
    return translated
