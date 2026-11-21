"""Skill catalog: describe huge numbers of skills, search them fast, load them on first use.

Entries live in SQLite (``~/.libra/catalog.db``) with an inverted index, and
search is BM25 computed over that index, so a query only touches the skills
that share its words. Nothing is connected or imported until a skill is
actually run: MCP servers start and APIs are called only then.

Catalog names are namespaced by where they came from, e.g.
``mcp.github.create_issue`` or ``api.petstore.get_pet_by_id``.
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import re
import sqlite3
import threading
from collections import Counter
from dataclasses import dataclass, field

from . import home
from .knowledge import terms

NAME_RE = re.compile(r"[a-z][a-z0-9_]*(\.[a-z0-9][a-z0-9_-]*)*")
K1, B = 1.2, 0.75


def safe_name(text: str) -> str:
    """Turn ``Get Pet By ID`` / ``getPetById`` / ``pets/{id}`` into ``get_pet_by_id``."""
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(text))
    text = re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower()
    if not text or not text[0].isalpha():
        text = "x_" + text
    return text[:80]


def index_terms(name: str, description: str, params: list[str]) -> list[str]:
    words = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name).replace(".", " ").replace("_", " ").replace("-", " ")
    return terms(words) * 2 + terms(description) + terms(" ".join(params))


@dataclass
class Entry:
    name: str
    description: str
    provider: str  # "mcp" or "openapi"
    source: str  # e.g. "mcp.github"
    risk: str = "read"
    spec: dict = field(default_factory=dict)

    @property
    def input_schema(self) -> dict:
        return self.spec.get("input_schema") or {"type": "object", "properties": {}}


class Catalog:
    def __init__(self, db_path: str | None = None):
        self.path = db_path or home.path("catalog.db")
        self._db = sqlite3.connect(self.path, check_same_thread=False)
        self._lock = threading.RLock()
        with self._lock, self._db:
            self._db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=NORMAL;
                CREATE TABLE IF NOT EXISTS skills(
                    id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, description TEXT, provider TEXT,
                    source TEXT, risk TEXT, spec TEXT, length INTEGER, added TEXT);
                CREATE INDEX IF NOT EXISTS skills_source ON skills(source);
                CREATE TABLE IF NOT EXISTS postings(term TEXT, skill INTEGER, tf INTEGER,
                    PRIMARY KEY(term, skill)) WITHOUT ROWID;
                CREATE INDEX IF NOT EXISTS postings_skill ON postings(skill);
                CREATE TABLE IF NOT EXISTS terms(term TEXT PRIMARY KEY, df INTEGER) WITHOUT ROWID;
                CREATE TABLE IF NOT EXISTS stats(key TEXT PRIMARY KEY, value REAL);
                INSERT OR IGNORE INTO stats VALUES ('n', 0), ('total_len', 0);
            """)

    def close(self) -> None:
        self._db.close()

    def __del__(self):
        try:
            self._db.close()
        except Exception:
            pass

    # ---------------------------------------------------------------- writes
    def _delete_ids(self, ids: list[int]) -> None:
        if not ids:
            return
        db = self._db
        for chunk in (ids[i : i + 900] for i in range(0, len(ids), 900)):
            marks = ",".join("?" * len(chunk))
            counts = db.execute(f"SELECT term, COUNT(*) FROM postings WHERE skill IN ({marks}) GROUP BY term",
                                chunk).fetchall()
            db.executemany("UPDATE terms SET df = df - ? WHERE term = ?", [(c, t) for t, c in counts])
            total = db.execute(f"SELECT COALESCE(SUM(length), 0) FROM skills WHERE id IN ({marks})", chunk).fetchone()[0]
            db.execute(f"DELETE FROM postings WHERE skill IN ({marks})", chunk)
            db.execute(f"DELETE FROM skills WHERE id IN ({marks})", chunk)
            db.execute("UPDATE stats SET value = value - ? WHERE key = 'n'", (len(chunk),))
            db.execute("UPDATE stats SET value = value - ? WHERE key = 'total_len'", (total,))
        db.execute("DELETE FROM terms WHERE df <= 0")

    def add_many(self, entries, replace_source: str | None = None) -> int:
        """Insert or replace entries. With ``replace_source``, that source's old entries are removed first."""
        now = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        added = 0
        with self._lock, self._db:
            db = self._db
            if replace_source:
                self._delete_ids([r[0] for r in db.execute("SELECT id FROM skills WHERE source = ?",
                                                           (replace_source,))])
            df_delta: Counter = Counter()
            n = total = 0
            for e in entries:
                if not NAME_RE.fullmatch(e.name):
                    raise ValueError(f"invalid catalog name {e.name!r}")
                old = db.execute("SELECT id FROM skills WHERE name = ?", (e.name,)).fetchone()
                if old:
                    self._delete_ids([old[0]])
                params = list((e.input_schema.get("properties") or {}).keys())
                tf = Counter(index_terms(e.name, e.description or "", params))
                length = sum(tf.values())
                cur = db.execute(
                    "INSERT INTO skills(name, description, provider, source, risk, spec, length, added) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (e.name, e.description or "", e.provider, e.source, e.risk, json.dumps(e.spec), length, now))
                db.executemany("INSERT INTO postings VALUES (?, ?, ?)", [(t, cur.lastrowid, c) for t, c in tf.items()])
                df_delta.update(tf.keys())
                n += 1
                total += length
                added += 1
            db.executemany("INSERT INTO terms VALUES (?, ?) ON CONFLICT(term) DO UPDATE SET df = df + excluded.df",
                           list(df_delta.items()))
            db.execute("UPDATE stats SET value = value + ? WHERE key = 'n'", (n,))
            db.execute("UPDATE stats SET value = value + ? WHERE key = 'total_len'", (total,))
        return added

    def remove_source(self, source: str) -> int:
        with self._lock, self._db:
            ids = [r[0] for r in self._db.execute("SELECT id FROM skills WHERE source = ? OR source LIKE ?",
                                                  (source, source + ".%"))]
            self._delete_ids(ids)
        return len(ids)

    # ----------------------------------------------------------------- reads
    def _row(self, row) -> Entry:
        name, description, provider, source, risk, spec = row
        return Entry(name, description, provider, source, risk, json.loads(spec or "{}"))

    def get(self, name: str) -> Entry | None:
        with self._lock:
            row = self._db.execute("SELECT name, description, provider, source, risk, spec FROM skills "
                                   "WHERE name = ?", (name,)).fetchone()
        return self._row(row) if row else None

    def count(self) -> int:
        with self._lock:
            return int(self._db.execute("SELECT value FROM stats WHERE key = 'n'").fetchone()[0])

    def sources(self) -> list[tuple[str, int]]:
        with self._lock:
            return self._db.execute("SELECT source, COUNT(*) FROM skills GROUP BY source ORDER BY source").fetchall()

    def search(self, query: str, limit: int = 10, source: str | None = None) -> list[tuple[float, Entry]]:
        """BM25 over names, descriptions and parameter names."""
        q = sorted(set(terms(re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", query).replace(".", " ").replace("_", " "))))
        if not q:
            return []
        with self._lock:
            db = self._db
            n, total = (v for (v,) in db.execute("SELECT value FROM stats WHERE key IN ('n', 'total_len') "
                                                 "ORDER BY key"))
            if not n:
                return []
            avg = total / n
            marks = ",".join("?" * len(q))
            dfs = dict(db.execute(f"SELECT term, df FROM terms WHERE term IN ({marks})", q).fetchall())
            weights = [(t, math.log(1 + (n - df + 0.5) / (df + 0.5))) for t, df in dfs.items()]
            if not weights:
                return []
            case = " ".join("WHEN ? THEN ?" for _ in weights)
            params = [x for tw in weights for x in tw]
            where = "p.term IN (" + ",".join("?" * len(weights)) + ")"
            params2 = [t for t, _ in weights]
            src_sql, src_params = "", []
            if source:
                src_sql = " AND (s.source = ? OR s.source LIKE ?)"
                src_params = [source, source + ".%"]
            sql = (f"SELECT s.name, s.description, s.provider, s.source, s.risk, s.spec, "
                   f"SUM((CASE p.term {case} END) * p.tf * {K1 + 1} / "
                   f"(p.tf + {K1} * (1 - {B} + {B} * s.length / ?))) AS score "
                   f"FROM postings p JOIN skills s ON s.id = p.skill WHERE {where}{src_sql} "
                   f"GROUP BY p.skill ORDER BY score DESC LIMIT ?")
            rows = db.execute(sql, params + [avg] + params2 + src_params + [limit]).fetchall()
        results = [(row[6], self._row(row[:6])) for row in rows]
        exact = self.get(query.strip())
        if exact and all(e.name != exact.name for _, e in results):
            results.insert(0, (float("inf"), exact))
        return results[:limit]


# -------------------------------------------------------------- providers
def make_runner(entry: Entry):
    """Callable(**kwargs) that runs a catalog entry; connections are made on first call."""
    if entry.provider == "mcp":
        from . import mcp

        return lambda **kwargs: mcp.call_tool(entry.spec["server"], entry.spec["tool"], kwargs)
    if entry.provider == "openapi":
        from . import openapi

        return lambda **kwargs: openapi.call_operation(entry.spec, kwargs)
    raise ValueError(f"unknown provider {entry.provider!r} for {entry.name}")


def coerce_by_schema(args: dict, schema: dict) -> dict:
    """Convert string arguments (from the command line or chat) to the JSON types the schema asks for."""
    props = schema.get("properties") or {}
    out = {}
    for key, value in args.items():
        kind = (props.get(key) or {}).get("type")
        if isinstance(kind, list):
            kind = next((k for k in kind if k != "null"), None)
        if isinstance(value, str):
            try:
                if kind == "integer":
                    value = int(value)
                elif kind == "number":
                    value = float(value)
                elif kind == "boolean":
                    value = value.strip().lower() in ("1", "true", "yes", "y", "on")
                elif kind in ("object", "array"):
                    value = json.loads(value)
            except (ValueError, json.JSONDecodeError) as exc:
                raise ValueError(f"{key}: expected {kind}, got {value!r}") from exc
        out[key] = value
    missing = [k for k in schema.get("required") or [] if k not in out]
    if missing:
        raise TypeError(f"missing required argument(s): {', '.join(missing)}")
    unknown = [k for k in out if props and k not in props and not schema.get("additionalProperties")]
    if unknown:
        raise TypeError(f"unknown argument(s) {', '.join(unknown)}; expected {', '.join(props)}")
    return out
