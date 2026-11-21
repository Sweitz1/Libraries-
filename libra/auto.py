"""Unattended training: collect text from the web and your files, filter it, train, keep only improvements.

One ``run`` does, in order:

1. **Lock** so two runs never overlap, then check free disk and dataset size.
2. **Collect** from the sources in the config (site crawls, single pages,
   RSS/Atom feeds, Project Gutenberg books, local folders), politely:
   robots.txt, delays, a page budget, and pages already seen unchanged are skipped.
3. **Filter**: minimum length, expected language, junk (symbol-heavy text),
   boilerplate lines (cookie banners, "share", "sign in"), repetitive pages
   and duplicates. E-mail addresses and phone numbers are masked.
4. **Train a copy** of the current model for ``steps_per_run`` steps
   (or a new one on the first run), within ``max_minutes``.
5. **Gate**: score the current and the new model on the same held-out text
   (never trained on). The new one replaces the current one only if it is
   not worse than ``max_regression`` allows. The replaced model is kept
   for ``rollback``.
6. **Report**: what was fetched, kept and dropped (and why), losses and the
   decision, in ``runs.jsonl`` and ``last-report.md``.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import shutil
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

import numpy as np

from . import checkpoint

TEMPLATE = {
    "name": "my-model",
    "enabled": True,
    "sources": [
        {"crawl": "https://example.com/docs/", "max_pages": 100, "depth": 2, "include": "", "exclude": ""},
        {"feed": "https://example.com/blog/feed.xml", "max_items": 20},
        {"sitemap": "https://example.com/sitemap.xml", "max_pages": 50},
        {"url": "https://example.com/about"},
        {"gutenberg": 1342},
        {"files": "~/my-notes"},
    ],
    "dataset": "datasets/auto",
    "model": "checkpoints/auto",
    "model_config": {"layers": 4, "embd": 128, "heads": 4, "ctx": 128, "vocab": 1024, "batch": 16,
                     "dropout": 0.1, "lr": 0.003},
    "steps_per_run": 2000,
    "filters": {"min_chars": 300, "language": "en", "min_language_confidence": 0.4, "max_symbol_ratio": 0.25,
                "max_repeated_line_ratio": 0.3, "mask_personal_info": True},
    "limits": {"max_pages_per_run": 300, "max_dataset_mb": 500, "max_minutes": 120, "min_free_disk_mb": 2000,
               "delay_seconds": 1.0, "retries": 2, "retry_delay_seconds": 5},
    "gate": {"holdout_fraction": 0.05, "max_regression": 0.02, "eval_suite": "", "max_eval_drop": 0.0},
    "discover": True,
    "notify": {"url": "", "on": "changes"},
    "allow_private": False,
    "device": "auto",
}

BOILERPLATE = re.compile(
    r"(cookie|cookies|privacy policy|terms of (use|service)|all rights reserved|subscribe|newsletter|sign (in|up)|"
    r"log ?in|log ?out|share (on|this)|follow us|skip to (main )?content|accept all|javascript|advertisement|"
    r"^©|copyright \d{4}|back to top|read more|click here|toggle navigation|main menu)", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
PHONE = re.compile(r"(?<![\w/])(\+?\d[\d ().-]{7,}\d)(?![\w/])")


# ------------------------------------------------------------------ config
def load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if path.endswith((".yaml", ".yml")):
        from .yamlish import loads

        cfg = loads(text)
    else:
        cfg = json.loads(text)
    merged = json.loads(json.dumps(TEMPLATE))  # deep copy of defaults
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    if not merged.get("sources"):
        raise ValueError("the config has no sources")
    base = os.path.dirname(os.path.abspath(path))
    for key in ("dataset", "model"):  # relative paths are relative to the config file
        merged[key] = os.path.join(base, os.path.expanduser(merged[key]))
    return merged


def write_template(path: str) -> None:
    if os.path.exists(path):
        raise FileExistsError(f"{path} already exists")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(TEMPLATE, f, indent=2)


# ----------------------------------------------------------------- filters
def mask_personal(text: str) -> tuple[str, int]:
    """Replace e-mail addresses and phone numbers (9+ digits) with placeholders; returns (text, how many)."""
    count = 0

    def phone(m):
        nonlocal count
        if sum(c.isdigit() for c in m.group()) >= 9:
            count += 1
            return "[phone]"
        return m.group()  # dates, ranges and short numbers stay

    text, emails = EMAIL.subn("[email]", text)
    return PHONE.sub(phone, text), count + emails


def clean(text: str) -> str:
    """Drop boilerplate and fragment lines; keep paragraphs and headings that lead into text."""
    lines = text.split("\n")
    kept = []
    for i, line in enumerate(lines):
        s = line.strip()
        if not s:
            kept.append("")
            continue
        words = len(s.split())
        if words <= 12 and BOILERPLATE.search(s):
            continue
        following = next((l for l in lines[i + 1:] if l.strip()), "")
        if words < 3 and len(following.split()) < 8:  # a stray fragment, not a heading that introduces text
            continue
        kept.append(s)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()


def check(text: str, f: dict) -> str | None:
    """Reason to drop the document, or None to keep it."""
    if len(text) < f["min_chars"]:
        return "too short"
    letters = sum(c.isalpha() for c in text)
    visible = sum(not c.isspace() for c in text) or 1
    if 1 - letters / visible > f["max_symbol_ratio"]:
        return "mostly symbols or numbers"
    lines = [l.strip().lower() for l in text.split("\n") if l.strip()]
    if len(lines) >= 5 and 1 - len(set(lines)) / len(lines) > f["max_repeated_line_ratio"]:
        return "repetitive"
    if f.get("language"):
        from .lang import code_for, detect

        want = code_for(f["language"]) or f["language"]
        guesses = dict(detect(text[:3000]))
        if guesses.get(want, 0) < f["min_language_confidence"]:
            top = max(guesses, key=guesses.get) if guesses else "unknown"
            return f"wrong language ({top})"
    return None


# ------------------------------------------------------------------- state
class State:
    """Seen pages and kept-document hashes, under ``<dataset>/.auto``."""

    def __init__(self, dataset: str):
        self.dir = os.path.join(dataset, ".auto")
        os.makedirs(self.dir, exist_ok=True)
        self.seen: dict = self._load("seen.json", {})  # url -> content hash
        self.hashes = set(self._load("hashes.json", []))
        self.discovered: dict = self._load("discovered.json", {})  # site -> {"feeds", "sitemaps", "checked"}

    def _load(self, name, default):
        p = os.path.join(self.dir, name)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        return default

    def save(self) -> None:
        for name, value in (("seen.json", self.seen), ("hashes.json", sorted(self.hashes)),
                            ("discovered.json", self.discovered)):
            p = os.path.join(self.dir, name)
            with open(p + ".tmp", "w", encoding="utf-8") as f:
                json.dump(value, f)
            os.replace(p + ".tmp", p)

    def lock(self) -> None:
        p = os.path.join(self.dir, "lock")
        if os.path.exists(p):
            with open(p) as f:
                pid = f.read().strip()
            if pid.isdigit() and _alive(int(pid)):
                raise RuntimeError(f"another run is in progress (pid {pid}); remove {p} if that's wrong")
        with open(p, "w") as f:
            f.write(str(os.getpid()))

    def unlock(self) -> None:
        try:
            os.remove(os.path.join(self.dir, "lock"))
        except FileNotFoundError:
            pass


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _hash(text: str) -> str:
    return hashlib.sha1(" ".join(text.lower().split()).encode("utf-8")).hexdigest()


# ----------------------------------------------------------------- collect
@dataclass
class Report:
    started: str = ""
    finished: str = ""
    fetched: int = 0
    unchanged: int = 0
    kept: int = 0
    kept_chars: int = 0
    dropped: dict = field(default_factory=dict)
    masked: int = 0
    errors: list = field(default_factory=list)
    stopped: list = field(default_factory=list)
    dataset_mb: float = 0.0
    trained: bool = False
    steps: int = 0
    old_loss: float | None = None
    new_loss: float | None = None
    old_eval: float | None = None
    new_eval: float | None = None
    decision: str = "no training"
    sample: str = ""
    seconds: float = 0.0
    retried: int = 0
    discovered: list = field(default_factory=list)

    def drop(self, reason: str) -> None:
        self.dropped[reason] = self.dropped.get(reason, 0) + 1

    def markdown(self, cfg: dict) -> str:
        lines = [f"# {cfg['name']}: run {self.started}", "",
                 f"- pages fetched: {self.fetched} (unchanged since last time: {self.unchanged})",
                 f"- documents kept: {self.kept} ({self.kept_chars:,} characters); masked e-mails/phones: {self.masked}",
                 "- dropped: " + (", ".join(f"{k}: {v}" for k, v in sorted(self.dropped.items())) or "none"),
                 f"- dataset size: {self.dataset_mb:.1f} MB"]
        if self.trained:
            old = "none (first model)" if self.old_loss is None else f"{self.old_loss:.4f}"
            lines += [f"- training: {self.steps} steps; held-out loss: old {old} → new {self.new_loss:.4f}"]
            if self.new_eval is not None:
                was = "none" if self.old_eval is None else f"{self.old_eval:.3f}"
                lines.append(f"- behaviour (eval suite accuracy): old {was} → new {self.new_eval:.3f}")
            lines.append(f"- **decision: {self.decision}**")
        else:
            lines.append(f"- **{self.decision}**")
        if self.sample:
            lines.append(f"- sample from the current model: {self.sample!r}")
        for s in self.stopped:
            lines.append(f"- limit: {s}")
        if self.errors:
            lines.append(f"- {len(self.errors)} error(s): " + "; ".join(self.errors[:8]))
        if self.discovered:
            lines.append("- found new sources: " + ", ".join(self.discovered[:6]) + (" …" if len(self.discovered) > 6 else ""))
        if self.retried:
            lines.append(f"- retried {self.retried} temporary network error(s)")
        lines.append(f"- took {self.seconds / 60:.1f} min")
        return "\n".join(lines) + "\n"


TRANSIENT = re.compile(r"timed out|could not fetch|HTTP (429|5\d\d)\b", re.I)


class _Retrying:
    """A Fetcher that retries temporary failures (timeouts, connection errors, HTTP 429/5xx) with backoff.

    Permanent problems (404, robots.txt, private addresses) are not retried."""

    def __init__(self, fetcher, retries: int, delay: float, report=None):
        self.fetcher, self.retries, self.delay, self.report = fetcher, retries, delay, report

    def get(self, url: str):
        from .web import FetchError

        for attempt in range(self.retries + 1):
            try:
                return self.fetcher.get(url)
            except FetchError as exc:
                if attempt == self.retries or not TRANSIENT.search(str(exc)):
                    raise
                if self.report is not None:
                    self.report.retried += 1
                time.sleep(self.delay * (2 ** attempt))

    def __getattr__(self, name):
        return getattr(self.fetcher, name)


def _fetcher(cfg: dict, report=None):
    from .web import Fetcher

    base = Fetcher(delay=cfg["limits"]["delay_seconds"], allow_private=bool(cfg.get("allow_private")))
    return _Retrying(base, int(cfg["limits"].get("retries", 2)), float(cfg["limits"].get("retry_delay_seconds", 5)),
                     report)


def _same_host(a: str, b: str) -> bool:
    from urllib.parse import urlsplit

    return urlsplit(a).netloc.lower() == urlsplit(b).netloc.lower()


def sitemap_urls(fetcher, url: str, limit: int, depth: int = 0) -> list[str]:
    """Page URLs from a sitemap or sitemap index, newest first when dates are given (same site only)."""
    if url.endswith(".gz"):
        raise ValueError(f"compressed sitemaps aren't supported: {url}")
    root = ET.fromstring(fetcher.get(url).text)
    entries = []
    for el in root:
        loc = lastmod = ""
        for child in el:
            name = child.tag.rsplit("}", 1)[-1]
            if name == "loc" and child.text:
                loc = child.text.strip()
            elif name == "lastmod" and child.text:
                lastmod = child.text.strip()
        if loc and _same_host(loc, url):
            entries.append((lastmod, loc))
    entries.sort(key=lambda e: e[0], reverse=True)
    if root.tag.rsplit("}", 1)[-1] == "sitemapindex":
        out = []
        for _, loc in entries:
            if depth < 2 and len(out) < limit:
                out += sitemap_urls(fetcher, loc, limit - len(out), depth + 1)
        return out[:limit]
    return [loc for _, loc in entries][:limit]


def discover(fetcher, site: str) -> dict:
    """Feeds and sitemaps a site advertises: robots.txt ``Sitemap:`` lines, ``/sitemap.xml`` and feed links."""
    from urllib.parse import urlsplit

    from .web import FetchError, normalize_url, parse_html

    parts = urlsplit(site)
    root = f"{parts.scheme}://{parts.netloc}"
    found = {"feeds": [], "sitemaps": []}
    try:
        robots = fetcher.request("GET", root + "/robots.txt", raise_for_status=False)
        if robots.status == 200:
            for line in robots.text.splitlines():
                if line.lower().startswith("sitemap:"):
                    sm = line.split(":", 1)[1].strip()
                    if _same_host(sm, root):
                        found["sitemaps"].append(sm)
        if not found["sitemaps"]:
            guess = fetcher.request("GET", root + "/sitemap.xml", raise_for_status=False)
            if guess.status == 200 and ("<urlset" in guess.text[:2000] or "<sitemapindex" in guess.text[:2000]):
                found["sitemaps"].append(root + "/sitemap.xml")
        page = fetcher.get(site)
        for link in parse_html(page.text).select("link[rel~=alternate]"):
            kind = (link.get("type") or "").lower()
            href = normalize_url(link.get("href", ""), page.url)
            if href and ("rss" in kind or "atom" in kind) and _same_host(href, root):
                found["feeds"].append(href)
    except (FetchError, ValueError, OSError):
        pass
    for key in found:
        found[key] = list(dict.fromkeys(found[key]))[:5]
    return found


def _expand_sources(cfg: dict, state: "State", fetcher, report: "Report", log) -> list[dict]:
    """The configured sources, each crawl followed by the feeds and sitemaps its site advertises."""
    out = []
    week = 7 * 24 * 3600
    for src in cfg["sources"]:
        out.append(src)
        if "crawl" not in src or not src.get("discover", cfg.get("discover", True)):
            continue
        site = src["crawl"]
        known = state.discovered.get(site)
        if not known or time.time() - known.get("checked", 0) > week:
            found = discover(fetcher, site)
            old = set((known or {}).get("feeds", [])) | set((known or {}).get("sitemaps", []))
            new = [u for u in found["feeds"] + found["sitemaps"] if u not in old]
            report.discovered += new
            if new and log:
                log(f"  found {len(new)} new source(s) on {site}")
            known = dict(found, checked=time.time())
            state.discovered[site] = known
        for feed in known.get("feeds", []):
            out.append({"feed": feed, "max_items": 10})
        for sm in known.get("sitemaps", []):
            out.append({"sitemap": sm, "max_pages": int(src.get("max_pages", 50)),
                        "include": src.get("include", ""), "exclude": src.get("exclude", "")})
    return out


def _feed_links(xml_text: str) -> list[str]:
    root = ET.fromstring(xml_text)
    links = []
    for el in root.iter():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "item":
            link = next((c.text for c in el if c.tag.rsplit("}", 1)[-1] == "link" and c.text), None)
            if link:
                links.append(link.strip())
        elif tag == "entry":
            for c in el:
                if c.tag.rsplit("}", 1)[-1] == "link" and c.get("href") and c.get("rel", "alternate") == "alternate":
                    links.append(c.get("href"))
                    break
    return links


def collect(cfg: dict, state: State, report: Report, log=print, progress=None):
    """Yield ``(url, title, text)`` for new or changed documents, within the page budget."""
    from .web import FetchError, crawl, extract

    budget = cfg["limits"]["max_pages_per_run"]
    fetcher = _fetcher(cfg, report)
    this_run: set = set()  # URLs already fetched in this run (a crawl and a sitemap often overlap)

    def page(url: str, title: str, text: str):
        this_run.add(url)
        report.fetched += 1
        if progress:
            progress.update(phase="collecting", fetched=report.fetched)
        digest = _hash(text)
        if state.seen.get(url) == digest:
            report.unchanged += 1
            return None
        state.seen[url] = digest
        return url, title, text

    for src in _expand_sources(cfg, state, fetcher, report, log):
        if report.fetched >= budget:
            report.stopped.append(f"page budget of {budget} reached")
            break
        left = budget - report.fetched
        try:
            if "crawl" in src:
                pages = crawl(src["crawl"], min(left, int(src.get("max_pages", 100))), int(src.get("depth", 2)),
                              cfg["limits"]["delay_seconds"], include=src.get("include") or None,
                              exclude=src.get("exclude") or None, fetcher=fetcher,
                              on_error=lambda u, e: report.errors.append(f"{u}: {e}"))
                for p in pages:
                    item = page(p.url, p.title, p.text)
                    if item:
                        yield item
            elif "feed" in src or "sitemap" in src:
                if "feed" in src:
                    links = _feed_links(fetcher.get(src["feed"]).text)[: int(src.get("max_items", 20))]
                else:
                    links = sitemap_urls(fetcher, src["sitemap"], int(src.get("max_pages", 50)) * 2)
                    inc = re.compile(src["include"]) if src.get("include") else None
                    exc_rx = re.compile(src["exclude"]) if src.get("exclude") else None
                    links = [u for u in links if (not inc or inc.search(u)) and not (exc_rx and exc_rx.search(u))]
                    links = links[: int(src.get("max_pages", 50))]
                links = [u for u in links if u not in this_run]
                for link in links[: budget - report.fetched]:
                    if not fetcher.allowed(link):
                        continue
                    try:
                        resp = fetcher.get(link)
                    except FetchError as exc:
                        report.errors.append(f"{link}: {exc}")
                        continue
                    data = extract(resp.text, resp.url) if resp.is_html else {"title": "", "text": resp.text}
                    item = page(resp.url, data["title"], data["text"])
                    if item:
                        yield item
            elif "url" in src:
                resp = fetcher.get(src["url"])
                data = extract(resp.text, resp.url) if resp.is_html else {"title": "", "text": resp.text}
                item = page(resp.url, data["title"], data["text"])
                if item:
                    yield item
            elif "gutenberg" in src:
                from .dataset import strip_gutenberg

                book = int(src["gutenberg"])
                url = f"https://www.gutenberg.org/cache/epub/{book}/pg{book}.txt"
                item = page(url, f"Gutenberg #{book}", strip_gutenberg(fetcher.get(url).text))
                if item:
                    yield item
            elif "files" in src:
                from .dataset import Dataset

                ds = Dataset(min_chars=1)
                ds.add_path(os.path.expanduser(src["files"]), log=lambda *a: None)
                for i, text in enumerate(ds.texts):
                    item = page(f"file:{src['files']}#{i}", "", text)
                    if item:
                        yield item
            else:
                report.errors.append(f"unknown source type: {src}")
        except (FetchError, ValueError, OSError, ET.ParseError) as exc:
            report.errors.append(f"{src}: {exc}")
            if log:
                log(f"  source failed: {exc}")


# ------------------------------------------------------------------- train
def holdout_loss(model_dir: str, texts: list[str], max_windows: int = 64) -> float:
    """Mean next-token loss on fixed windows of held-out text (lower is better)."""
    from . import checkpoint
    from .autograd import no_grad
    from .tokenizer import EOT

    model, tok, _ = checkpoint.load(model_dir)
    ids = np.array(tok.encode("".join(t + EOT for t in texts)), dtype=np.int64)
    block = min(model.config.block_size, len(ids) - 1)
    if block < 2:
        raise ValueError("held-out text is too short to score")
    starts = np.unique(np.linspace(0, len(ids) - block - 1, num=max_windows).astype(int))
    losses = []
    with no_grad():
        for chunk in np.array_split(starts, max(1, len(starts) // 8)):
            x = np.stack([ids[s : s + block] for s in chunk])
            y = np.stack([ids[s + 1 : s + block + 1] for s in chunk])
            losses.append(model(x, y)[1].item() * len(chunk))
    return float(sum(losses) / len(starts))


def _read_texts(path: str) -> list[str]:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l)["text"] for l in f if l.strip()]


def _dir_mb(path: str) -> float:
    total = 0
    for root, _, files in os.walk(path):
        total += sum(os.path.getsize(os.path.join(root, f)) for f in files)
    return total / 1e6


class Progress:
    """Live state of a run in ``<dataset>/.auto/progress.json``, read by the app."""

    def __init__(self, state_dir: str):
        self.path = os.path.join(state_dir, "progress.json")
        self.data: dict = {}
        self._last = 0.0

    def update(self, **values) -> None:
        self.data.update(values, updated=_dt.datetime.now().astimezone().isoformat(timespec="seconds"))
        now = time.time()
        if "phase" in values and values["phase"] != self.data.get("_phase") or now - self._last > 1.0:
            self.data["_phase"] = self.data.get("phase")
            self._last = now
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in self.data.items() if not k.startswith("_")}, f)
            os.replace(tmp, self.path)


def progress(config_path: str) -> dict:
    cfg = load_config(config_path)
    path = os.path.join(cfg["dataset"], ".auto", "progress.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def is_running(config_path: str) -> bool:
    """True if a run holds the lock right now (its process is alive)."""
    cfg = load_config(config_path)
    path = os.path.join(cfg["dataset"], ".auto", "lock")
    try:
        with open(path) as f:
            pid = f.read().strip()
    except OSError:
        return False
    return pid.isdigit() and _alive(int(pid))


def notify(cfg: dict, report: "Report", log=print) -> bool:
    """POST a short report to ``notify.url`` (works with ntfy, Gotify-style or your own webhook)."""
    n = cfg.get("notify") or {}
    url = (n.get("url") or "").strip()
    if not url:
        return False
    when = n.get("on", "changes")
    changed = report.decision.startswith("kept the new model")
    failed = "failed" in report.decision or report.decision.startswith("time limit") or bool(report.errors)
    if (when == "changes" and not (changed or failed)) or (when == "failures" and not failed):
        return False
    from .notify import send

    ok = send(url, f"Libra {cfg['name']}: {report.decision}", report.markdown(cfg),
              allow_private=bool(cfg.get("allow_private") or n.get("allow_private")))
    if not ok and log:
        log("  notification failed")
    return ok


def run(config_path: str, dry_run: bool = False, log=print) -> Report:
    cfg = load_config(config_path)
    if not cfg.get("enabled", True) and not dry_run:
        return Report(started=_dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                      decision=f'training is switched off in {config_path}: choose your sources, then set "enabled": true')
    dataset, model_dir = cfg["dataset"], cfg["model"]
    train_dir, holdout_path = os.path.join(dataset, "train"), os.path.join(dataset, "holdout.jsonl")
    os.makedirs(train_dir, exist_ok=True)
    state = State(dataset)
    report = Report(started=_dt.datetime.now().astimezone().isoformat(timespec="seconds"))
    t0 = time.time()
    state.lock()
    prog = Progress(state.dir)
    prog.update(phase="starting", started=report.started, fetched=0, kept=0, step=0, steps=0, loss=None)
    failure = None
    try:
        free_mb = shutil.disk_usage(dataset).free / 1e6
        if free_mb < cfg["limits"]["min_free_disk_mb"]:
            raise RuntimeError(f"only {free_mb:.0f} MB free; the config asks for {cfg['limits']['min_free_disk_mb']} MB")
        full = _dir_mb(train_dir) >= cfg["limits"]["max_dataset_mb"]
        if full:
            report.stopped.append(f"dataset reached {cfg['limits']['max_dataset_mb']} MB; collecting skipped")

        # --- collect and filter
        new_train, new_holdout = [], []
        holdout_every = max(2, round(1 / max(1e-6, cfg["gate"]["holdout_fraction"])))
        f = cfg["filters"]
        if not full:
            if log:
                log("collecting…")
            for url, title, text in collect(cfg, state, report, log, prog):
                text = clean(((title + "\n\n") if title else "") + text)
                reason = check(text, f)
                if reason:
                    report.drop(reason)
                    continue
                digest = _hash(text)
                if digest in state.hashes:
                    report.drop("duplicate")
                    continue
                if f.get("mask_personal_info"):
                    text, n = mask_personal(text)
                    report.masked += n
                state.hashes.add(digest)
                record = {"text": text, "url": url, "added": report.started}
                # every Nth kept document goes to the held-out set used by the gate
                if (len(state.hashes) % holdout_every == 0) or not os.path.exists(holdout_path) and not new_holdout:
                    new_holdout.append(record)
                else:
                    new_train.append(record)
                report.kept += 1
                report.kept_chars += len(text)
                prog.update(kept=report.kept)
        if not dry_run:
            if new_train:
                stamp = report.started.replace(":", "").replace("-", "")[:15]
                with open(os.path.join(train_dir, f"run-{stamp}-{os.getpid()}.jsonl"), "a", encoding="utf-8") as out:
                    for r in new_train:
                        out.write(json.dumps(r, ensure_ascii=False) + "\n")
            if new_holdout:
                with open(holdout_path, "a", encoding="utf-8") as out:
                    for r in new_holdout:
                        out.write(json.dumps(r, ensure_ascii=False) + "\n")
            state.save()
        report.dataset_mb = _dir_mb(train_dir)
        if log:
            log(f"kept {report.kept} new document(s); dropped {sum(report.dropped.values())}")

        # --- train and gate
        has_train = any(n.endswith(".jsonl") for n in os.listdir(train_dir))
        holdout = _read_texts(holdout_path)
        if dry_run:
            report.decision = "dry run: nothing written or trained"
        elif not has_train or not holdout:
            report.decision = "not enough data to train yet"
        elif not new_train and checkpoint.exists(model_dir):
            report.decision = ("no new text since the last run; model unchanged" if not new_holdout else
                               "the new documents all went to the held-out test set; model unchanged")
        else:
            report.trained = True
            _train_and_gate(cfg, train_dir, holdout, model_dir, report, t0, log, prog)
        if checkpoint.exists(model_dir):
            report.sample = _sample(model_dir)
    except Exception as exc:  # recorded and reported, then raised so the service shows it failed
        failure = exc
        report.decision = f"run failed: {exc}"
        report.errors.append(str(exc))
        raise
    finally:
        state.unlock()
        prog.update(phase="failed" if failure else "done", decision=report.decision)
        report.seconds = time.time() - t0
        report.finished = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
        if not dry_run:
            with open(os.path.join(state.dir, "runs.jsonl"), "a", encoding="utf-8") as out:
                out.write(json.dumps(report.__dict__, ensure_ascii=False) + "\n")
            with open(os.path.join(state.dir, "last-report.md"), "w", encoding="utf-8") as out:
                out.write(report.markdown(cfg))
            notify(cfg, report, log)
    return report


def _train_and_gate(cfg, train_dir, holdout, model_dir, report: Report, t0: float, log, prog=None) -> None:
    from .train import TrainConfig, train

    mc = cfg["model_config"]
    candidate = model_dir.rstrip("/") + ".candidate"
    shutil.rmtree(candidate, ignore_errors=True)
    exists = checkpoint.exists(model_dir)
    start = 0
    if exists:
        shutil.copytree(model_dir, candidate)
        start = int(checkpoint.read_meta(model_dir).get("step", 0))
    remaining = cfg["limits"]["max_minutes"] * 60 - (time.time() - t0)
    if remaining < 30:
        report.decision = "time limit reached before training"
        report.trained = False
        return
    steps = int(cfg["steps_per_run"])
    tc = TrainConfig(data=[train_dir], out=candidate, vocab_size=int(mc["vocab"]), block_size=int(mc["ctx"]),
                     n_layer=int(mc["layers"]), n_head=int(mc["heads"]), n_embd=int(mc["embd"]),
                     dropout=float(mc["dropout"]), batch_size=int(mc["batch"]), lr=float(mc["lr"]),
                     steps=start + steps, warmup=min(100, max(1, steps // 10)), eval_every=max(50, steps // 4),
                     log_every=max(10, steps // 20), resume=exists, device=cfg.get("device", "auto"),
                     max_seconds=remaining - 20)
    if log:
        log(f"training {'(resuming)' if exists else '(new model)'} for up to {steps} steps…")
    if prog:
        prog.update(phase="training", step=0, steps=steps)
    try:
        train(tc, log=log or (lambda *a: None),
              progress=(lambda step, total, loss: prog.update(step=step - start, steps=steps, loss=round(loss, 4)))
              if prog else None)
    except Exception as exc:  # e.g. too little text for the context length; the current model is untouched
        shutil.rmtree(candidate, ignore_errors=True)
        report.trained = False
        report.decision = f"training failed, current model unchanged: {exc}"
        report.errors.append(str(exc))
        return
    report.steps = int(checkpoint.read_meta(candidate).get("step", 0)) - start
    if prog:
        prog.update(phase="scoring")
    report.new_loss = holdout_loss(candidate, holdout)
    report.old_loss = holdout_loss(model_dir, holdout) if exists else None
    allowed = None if report.old_loss is None else report.old_loss * (1 + cfg["gate"]["max_regression"])
    behaviour_ok, why = True, ""
    suite = _eval_suite(cfg)
    if suite and (allowed is None or report.new_loss <= allowed):
        # lower loss alone doesn't mean better answers: the behaviour score must not drop either
        from .evaluate import evaluate_model

        report.new_eval = evaluate_model(candidate, suite=suite)["choice_accuracy"]
        report.old_eval = evaluate_model(model_dir, suite=suite)["choice_accuracy"] if exists else None
        drop = float(cfg["gate"].get("max_eval_drop", 0.0))
        if report.old_eval is not None and report.new_eval is not None and report.new_eval < report.old_eval - drop:
            behaviour_ok = False
            why = (f"its behaviour score dropped ({report.new_eval:.3f} < {report.old_eval:.3f} on "
                   f"{os.path.basename(suite)})")
    if (allowed is None or report.new_loss <= allowed) and behaviour_ok:
        previous = model_dir.rstrip("/") + ".previous"
        shutil.rmtree(previous, ignore_errors=True)
        if exists:
            os.replace(model_dir, previous)
        os.replace(candidate, model_dir)
        report.decision = "kept the new model" + ("" if allowed is None else " (held-out loss did not get worse)")
    else:
        rejected = model_dir.rstrip("/") + ".rejected"
        shutil.rmtree(rejected, ignore_errors=True)
        os.replace(candidate, rejected)
        reason = why or f"it scored worse on held-out text ({report.new_loss:.4f} > {allowed:.4f})"
        report.decision = f"kept the old model: {reason}; the new one is saved in {rejected}"


def _eval_suite(cfg) -> str:
    """The behaviour suite the gate uses: "" (none), "builtin", or a path to a JSON Lines suite."""
    value = str(cfg["gate"].get("eval_suite") or "").strip()
    if not value:
        return ""
    if value == "builtin":
        from .evaluate import DEFAULT_MODEL_SUITE

        return DEFAULT_MODEL_SUITE
    if not os.path.exists(value):
        raise ValueError(f"gate.eval_suite: no such file {value!r}")
    return value


def _sample(model_dir: str) -> str:
    from . import checkpoint
    from .generate import SamplingParams, generate

    model, tok, _ = checkpoint.load(model_dir)
    return generate(model, tok, "", SamplingParams(max_tokens=40, temperature=0.8, seed=0)).replace("\n", " ")[:160]


def rollback(config_path: str) -> str:
    cfg = load_config(config_path)
    model_dir = cfg["model"]
    previous = model_dir.rstrip("/") + ".previous"
    if not os.path.exists(previous):
        raise RuntimeError("there is no previous model to go back to")
    swap = model_dir.rstrip("/") + ".swap"
    os.replace(model_dir, swap)
    os.replace(previous, model_dir)
    os.replace(swap, previous)
    return model_dir


def last_run(config_path: str) -> dict:
    """The latest finished run's time and decision (from any trigger: the timer, the app, the terminal)."""
    cfg = load_config(config_path)
    path = os.path.join(cfg["dataset"], ".auto", "runs.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            lines = [l for l in f if l.strip()]
    except OSError:
        return {}
    if not lines:
        return {}
    rec = json.loads(lines[-1])
    return {"finished": rec.get("finished", ""), "decision": rec.get("decision", "")}


def status(config_path: str) -> str:
    cfg = load_config(config_path)
    path = os.path.join(cfg["dataset"], ".auto", "last-report.md")
    if not os.path.exists(path):
        return f"{cfg['name']}: no runs yet (libra auto run {config_path})"
    runs = os.path.join(cfg["dataset"], ".auto", "runs.jsonl")
    with open(runs, encoding="utf-8") as f:
        history = [json.loads(l) for l in f if l.strip()]
    kept = sum(1 for r in history if r.get("decision", "").startswith("kept the new"))
    with open(path, encoding="utf-8") as f:
        last = f.read()
    return f"{len(history)} run(s), {kept} improved the model\n\n{last}"


def schedule_lines(config_path: str, at: str) -> dict:
    """Commands to run ``libra auto run`` every day at ``at`` (HH:MM)."""
    import sys

    m = re.fullmatch(r"(\d{1,2}):(\d{2})", at)
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise ValueError("--at must be HH:MM, e.g. 02:30")
    cfg_abs = os.path.abspath(config_path)
    log = os.path.join(os.path.dirname(cfg_abs), "libra-auto.log")
    cmd = f'"{sys.executable}" -m libra auto run "{cfg_abs}"'
    return {
        "cron": f"{int(m.group(2))} {int(m.group(1))} * * * {cmd} >> \"{log}\" 2>&1",
        "windows": f'schtasks /Create /SC DAILY /ST {int(m.group(1)):02d}:{m.group(2)} /TN "Libra auto training" '
                   f'/TR "{cmd.replace(chr(34), chr(92) + chr(34))}"',
    }
