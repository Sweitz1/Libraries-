"""Libra plugins for fanfiction.net: the semi-structured bits it *does* expose.

Fanfiction.net has no infobox and no API (unlike a Fandom/MediaWiki wiki — see ``plugins/lorebook.py``),
but a story page and a community page both carry a machine-readable *metadata line* — rating, language,
genre, characters, chapters, words, reviews, favs, follows, status, dates — and the fandom breadcrumb.
These skills read exactly that, rather than (mostly) the story prose:

  - ``fic_meta``       one story page (``/s/<id>/…``) -> its fandom(s) and metadata fields.
  - ``fic_community``  a community page (``/community/<name>/<id>/``) -> the list of collected stories,
                       each with its own title, author, summary and metadata.
  - ``fic_text``       one chapter -> its story text, so a codex/tech-readout chapter can be read or added.
  - ``fic_spec``       a chapter + a subject name (e.g. a fan-made ship) -> a *spec sheet* built from the
                       story's own codex/tech-readout block, optionally cross-referenced to canon wikis.
  - ``fic_progression``a chapter (or range) + a character -> the new abilities/skills/levels they gain, from
                       LitRPG ``[Skill]``/``[Level Up]`` notifications and "learned/gained/unlocked" prose.

    libra skill run fic_meta https://www.fanfiction.net/s/11111/1/
    libra skill run fic_community https://www.fanfiction.net/community/Crossovers/1076/ --out list.json
    libra skill run fic_spec https://www.fanfiction.net/s/11111/7/ Ragnarok \
        --wikis https://stargate.fandom.com,https://battlestarwiki.fandom.com --out ragnarok.json

Canon characters/ships have real specs on their fandom wiki (use ``worldbook``); a *fan-made* ship like a
crossover's flagship has specs only in the fic's own codex, which ``fic_spec`` reads. Fanfiction.net sits
behind a bot filter, so a live fetch may return a challenge page instead of HTML; the skills report that
plainly. All fetching goes through Libra's guarded fetcher. Accept once: ``libra trust accept-plugin
plugins/fanfiction.py``.
"""
from __future__ import annotations

import json
import os
import re
from urllib.parse import urlsplit

from libra import wiki as _wiki
from libra.skills import SkillResult, fetcher, skill

# Bare tokens in the metadata line are classified by these; anything left over is the character list.
_LANGUAGES = {"english", "spanish", "french", "german", "portuguese", "italian", "dutch", "polish",
              "russian", "chinese", "japanese", "korean", "indonesian", "filipino", "hungarian",
              "swedish", "norwegian", "danish", "finnish", "greek", "turkish", "czech", "romanian",
              "catalan", "ukrainian", "hebrew", "arabic", "vietnamese", "thai", "hindi", "esperanto"}
_GENRES = {"adventure", "angst", "romance", "humor", "drama", "poetry", "parody", "mystery", "fantasy",
           "horror", "supernatural", "suspense", "sci-fi", "friendship", "crime", "family", "hurt",
           "comfort", "tragedy", "western", "spiritual"}
# key: value pairs that may appear in the line
_FIELD_KEYS = {"chapters", "words", "reviews", "favs", "follows", "updated", "published", "status", "id"}


def _fetch(url: str):
    if not re.match(r"https?://", url, re.I):
        raise ValueError("a http(s) fanfiction.net URL is required")
    resp = fetcher().get(url)
    return resp


def _looks_blocked(resp) -> bool:
    low = resp.text[:2000].lower()
    return ("just a moment" in low or "cf-browser-verification" in low or "enable javascript and cookies"
            in low or "attention required" in low)


def _classify_bare(token: str, meta: dict):
    low = token.lower()
    if low in _LANGUAGES and "language" not in meta:
        meta["language"] = token
        return
    parts = [p.strip().lower() for p in low.split("/") if p.strip()]
    if parts and all(p in _GENRES for p in parts) and "genre" not in meta:
        meta["genre"] = token
        return
    meta.setdefault("characters", token)          # the one remaining bare token is the character list


def _parse_meta_line(text: str) -> dict:
    """Parse a fanfiction.net metadata line into fields. Tolerant of missing/extra parts."""
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"^crossover\s*-\s*", "", text, flags=re.I)   # community rows prefix this
    meta: dict = {}
    for raw in text.split(" - "):
        token = raw.strip()
        if not token:
            continue
        key, sep, val = token.partition(":")
        key_l = key.strip().lower()
        if key_l == "rated":
            meta["rating"] = re.sub(r"^fiction\s+", "", val.strip(), flags=re.I)
        elif sep and key_l in _FIELD_KEYS:
            meta[key_l] = val.strip()
        else:
            _classify_bare(token, meta)
    return meta


def _first_user_link(node):
    for a in node.select("a[href^=/u/]"):
        if a.text:
            return a.text
    return ""


def _meta_node_text(scope):
    """The text of the span holding the 'Rated: … - Words: …' line, if present."""
    for n in scope.iter():
        if n.tag in ("span", "div") and "Words:" in n.text and " - " in n.text:
            return n.text
    return ""


# --------------------------------------------------------------- story page
def _route_meta(message: str):
    m = re.search(r"(https?://(?:www\.)?fanfiction\.net/s/\d+\S*)", message, re.I)
    return {"url": m.group(1)} if m else None


@skill("fic_meta", "Read a fanfiction.net story's fandom and metadata (rating, genre, characters, words, "
       "status …) — the catalogue data, not the story text.",
       {"url": "the story URL, e.g. https://www.fanfiction.net/s/11111/1/",
        "out": "optional .json file to write the record to"},
       router=_route_meta, risk="read", external=True)
def fic_meta(url: str, out: str = "") -> SkillResult:
    from libra.web import parse_html

    resp = _fetch(url)
    if resp.status != 200:
        return SkillResult("fic_meta", False, f"{url} returned HTTP {resp.status}.")
    if _looks_blocked(resp):
        return SkillResult("fic_meta", False,
                           "fanfiction.net returned its bot-check page instead of the story (it blocks "
                           "automated fetches). Open the page in a browser, save the HTML, and read that.")
    doc = parse_html(resp.text)
    top = doc.select_one("#profile_top") or doc
    title_node = top.select_one("b.xcontrast_txt") or top.select_one("b") or doc.find("title")
    author = _first_user_link(top)
    summary_node = top.select_one("div.xcontrast_txt")
    fandoms = [a.text for a in doc.select("#pre_story_links a") if a.text]
    meta = _parse_meta_line(_meta_node_text(top))
    record = {"url": url, "title": title_node.text if title_node else "", "author": author,
              "summary": summary_node.text if summary_node else "",
              "fandoms": fandoms, "crossover": len(fandoms) > 1 or any("Crossover" in f for f in fandoms),
              **meta}
    if not record["title"] and not meta:
        return SkillResult("fic_meta", False,
                           f"Couldn't find a story on {url}. Make sure it's a story page (…/s/<id>/…).")
    if out:
        with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return SkillResult("fic_meta", True, _render_story(record, out), record)


def _render_story(r: dict, out: str) -> str:
    lines = [f"# {r['title']}" + (f" — by {r['author']}" if r["author"] else "")]
    if r["fandoms"]:
        lines.append(("Crossover: " if r["crossover"] else "Fandom: ") + " × ".join(r["fandoms"]))
    if r["summary"]:
        lines += ["", r["summary"]]
    facts = [(k, r[k]) for k in ("rating", "language", "genre", "characters", "chapters", "words",
                                 "reviews", "favs", "follows", "status", "updated", "published") if r.get(k)]
    if facts:
        lines += ["", "## Metadata"] + [f"- **{k}:** {v}" for k, v in facts]
    lines += ["", f"Source: {r['url']}  [fanfiction.net]"]
    if out:
        lines += ["", f"[record written to {out}]"]
    return "\n".join(lines)


# ------------------------------------------------------ story text & codex
def _story_text(doc) -> str:
    node = (doc.select_one("#storytext") or doc.select_one(".storytext")
            or doc.select_one("#storycontent") or doc.select_one("#chapters"))
    return node.block_text() if node is not None else ""


@skill("fic_text", "Get a fanfiction.net chapter's story text, so a codex/tech-readout chapter can be read "
       "or added to your files.",
       {"url": "the chapter URL, e.g. https://www.fanfiction.net/s/11111/7/", "out": "optional .txt to write"},
       risk="read", external=True)
def fic_text(url: str, out: str = "") -> SkillResult:
    from libra.web import parse_html

    resp = _fetch(url)
    if resp.status != 200:
        return SkillResult("fic_text", False, f"{url} returned HTTP {resp.status}.")
    if _looks_blocked(resp):
        return SkillResult("fic_text", False,
                           "fanfiction.net returned its bot-check page instead of the chapter. Open it in a "
                           "browser, save the HTML, and read that.")
    text = _story_text(parse_html(resp.text)).strip()
    if not text:
        return SkillResult("fic_text", False,
                           f"Couldn't find the story text on {url}. Make sure it's a chapter page (…/s/<id>/<n>/).")
    if out:
        with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
            f.write(text + "\n")
    shown = text if len(text) <= 4000 else text[:4000].rstrip() + f"\n… ({len(text) - 4000:,} more characters)"
    note = f"\n\n[{len(text):,} characters" + (f", written to {out}" if out else "") + "]"
    return SkillResult("fic_text", True, shown + note, {"url": url, "characters": len(text), "text": text})


# A spec line in a codex / tech readout: "Label: value", "Label  value" (tab) — colon or tab, not prose dashes.
_SPEC_LINE = re.compile(r"^\s*[-*•‣●]?\s*([A-Za-z][\w /()'’.&-]{1,38}?)\s*[:：\t]\s*(\S.*)$")
_HEADER_RE = re.compile(r"spec(ification)?s?|tech(nical)?\s*readout|codex|stat(istic)?s|data\s*sheet|dossier|"
                        r"readout|loadout|schematics?", re.I)
# Capitalised proper-noun runs and model codes (BC-304, Mark III) referenced in the specs.
_MENTION = re.compile(r"\b([A-Z][A-Za-z0-9]+(?:[ -][A-Z0-9][A-Za-z0-9]*)*|[A-Z]{2,}-?\d+[A-Za-z]*)\b")
_MENTION_STOP = {"the", "and", "a", "an", "of", "for", "with", "from", "to", "in", "on", "by", "its",
                 "modified", "enhanced", "primary", "secondary", "standard", "main", "heavy", "point",
                 "advanced", "experimental", "dual", "twin", "class", "type", "mark", "mk"}


def _spec_runs(lines: list) -> list:
    """Contiguous runs of >=2 spec lines (blank lines don't break a run). -> [(start_index, [(i,k,v)…])]."""
    runs, cur, start = [], [], None
    for i, line in enumerate(lines):
        if not line.strip():
            continue                                             # blank line: keep the run going
        m = _SPEC_LINE.match(line)
        if m:
            if not cur:
                start = i
            cur.append((i, m.group(1).strip(), m.group(2).strip()))
        else:
            if len(cur) >= 2:
                runs.append((start, cur))
            cur = []
    if len(cur) >= 2:
        runs.append((start, cur))
    return runs


def _pick_run(lines: list, runs: list, subject: str):
    """Choose the spec run most associated with the subject; return (start, run, score)."""
    sub = subject.lower()
    best, best_score = None, -1
    for start, run in runs:
        ctx = " ".join(lines[max(0, start - 5):start + 1]).lower()
        body = " ".join(k + " " + v for _, k, v in run).lower()
        score = len(run) + (120 if sub in ctx else 0) + (30 if sub in body else 0)
        if _HEADER_RE.search(" ".join(lines[max(0, start - 5):start])):
            score += 15
        if score > best_score:
            best, best_score = (start, run), score
    return (best[0], best[1], best_score) if best else (None, None, -1)


def _mentions(run: list, subject: str) -> list:
    found: list = []
    for _, _, value in run:
        for m in _MENTION.finditer(value):
            term = m.group(1).strip()
            words = term.split()
            while words and words[0].lower() in _MENTION_STOP:   # drop leading "Modified", "Heavy", …
                words = words[1:]
            term = " ".join(words)
            if not term or term.lower() == subject.lower() or term.lower() in _MENTION_STOP:
                continue
            has_digit = any(c.isdigit() for c in term)
            if len(term) < 3 and not has_digit:
                continue
            if term.isupper() and len(term) <= 4 and not has_digit:  # drop "III", "FTL"-style noise
                continue
            if term not in found:
                found.append(term)
    return found[:15]


def _gloss(mentions: list, wikis: list, fetch, cap: int = 8) -> dict:
    """One-line canon summary for each referenced term, from the given wikis."""
    apis = []
    for w in wikis:
        api = _wiki.resolve_api(w, mentions[0] if mentions else "a", fetch)
        if api:
            apis.append(api)
    out: dict = {}
    for term in mentions[:cap]:
        for api in apis:
            titles = _wiki.api_search(api, term, 1, fetch) or []
            if titles:
                summary = _wiki.page_summary(api, titles[0], fetch, 240)
                if summary:
                    root = "{0.scheme}://{0.netloc}".format(urlsplit(api))
                    out[term] = {"wiki": root, "title": titles[0], "summary": summary}
                    break
    return out


def _route_spec(message: str):
    m = re.search(r"spec(?:s|\s*sheet)?\b.*?(https?://(?:www\.)?fanfiction\.net/s/\d+\S*)\s+(\w[\w '-]*)", message, re.I)
    return {"url": m.group(1), "subject": m.group(2).strip()} if m else None


@skill("fic_spec", "Build a spec sheet for a ship/craft/character from a fanfiction's own codex or "
       "tech-readout, optionally cross-referenced to canon wikis. For fan-made things with no wiki page.",
       {"url": "the chapter URL that holds the codex, e.g. https://www.fanfiction.net/s/11111/7/",
        "subject": "what to build the sheet for, e.g. Ragnarok",
        "wikis": "optional comma-separated canon wikis to gloss referenced tech, e.g. "
                 "https://stargate.fandom.com,https://battlestarwiki.fandom.com",
        "out": "optional .json file to write the spec sheet to"},
       router=_route_spec, risk="read", external=True)
def fic_spec(url: str, subject: str, wikis: str = "", out: str = "") -> SkillResult:
    from libra.web import parse_html

    if not subject.strip():
        raise ValueError("fic_spec needs a subject, e.g. the ship's name")
    resp = _fetch(url)
    if resp.status != 200:
        return SkillResult("fic_spec", False, f"{url} returned HTTP {resp.status}.")
    if _looks_blocked(resp):
        return SkillResult("fic_spec", False,
                           "fanfiction.net returned its bot-check page instead of the chapter. Open it in a "
                           "browser, save the HTML, and read that.")
    text = _story_text(parse_html(resp.text))
    if not text.strip():
        return SkillResult("fic_spec", False,
                           f"Couldn't find the story text on {url} (is it a chapter page, …/s/<id>/<n>/?).")
    lines = text.split("\n")
    runs = _spec_runs(lines)
    if not runs:
        return SkillResult("fic_spec", False,
                           f"No codex/tech-readout block (lines like “Length: 1,900 m”) found in this chapter. "
                           f"Point fic_spec at the chapter that holds {subject}’s specifications.")
    start, run, score = _pick_run(lines, runs, subject)
    specs: dict = {}
    for _, key, value in run:
        specs.setdefault(key, value)
    heading = next((line.strip() for line in reversed(lines[max(0, start - 5):start]) if line.strip()), "")
    mentions = _mentions(run, subject)
    record = {"subject": subject, "url": url, "source": "fanfiction.net codex", "heading": heading,
              "spec_count": len(specs), "specs": specs, "mentions": mentions}
    if score < 30:                                               # subject not seen next to the chosen block
        record["note"] = (f"“{subject}” wasn’t found next to this spec block; this is the closest/only one "
                          "in the chapter — check it’s the right subject.")
    if wikis.strip():
        gloss = _gloss(mentions, [w.strip() for w in wikis.split(",") if w.strip()], fetcher(delay=0.3).get)
        if gloss:
            record["canon"] = gloss
    if out:
        with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return SkillResult("fic_spec", True, _render_spec(record, out), record)


def _render_spec(r: dict, out: str) -> str:
    lines = [f"# {r['subject']} — spec sheet"]
    if r.get("heading"):
        lines.append(f"_{r['heading']}_")
    lines += ["", "## Specifications"] + [f"- **{k}:** {v}" for k, v in r["specs"].items()]
    if r.get("mentions"):
        lines += ["", "Referenced tech / factions: " + ", ".join(r["mentions"])]
    if r.get("canon"):
        lines += ["", "## Canon cross-reference"]
        lines += [f"- **{term}** — {g['title']} ({g['wiki']}): {g['summary']}" for term, g in r["canon"].items()]
    if r.get("note"):
        lines += ["", f"Note: {r['note']}"]
    lines += ["", f"Source: {r['url']}  [fanfiction.net codex]"]
    if out:
        lines += ["", f"[spec sheet written to {out}]"]
    return "\n".join(lines)


# ----------------------------------------------- character evolution / progression
# A LitRPG "system" notification is bracketed, e.g. [Skill – Fireball obtained], [Level Up!], [Class: Mage].
_SYS_KEY = re.compile(r"skill|level|class|spell|feat|abilit|power|title|rank|quest|learned|gained|obtain|"
                      r"unlock|evolv|\bnew\b|\bup\b|xp|experience|proficien|cantrip|mana|perk|trait", re.I)
# Prose cues that a character gained a new capability.
_PROG_CUE = re.compile(r"\b(learn(?:s|ed|t)?|gain(?:s|ed)?|acquir\w+|master(?:s|ed)?|unlock\w+|obtain\w+|"
                       r"develop(?:s|ed)?|discover\w+|awaken\w+|evolv\w+|level(?:s|ed)?\s*up|"
                       r"reach(?:es|ed)?\s+(?:level|rank)|can now (?:cast|use|summon|wield|do|create)|"
                       r"new (?:spell|skill|feat|power|abilit\w+|class|title|rank))\b", re.I)


def _sentences(text: str) -> list:
    return re.split(r"(?<=[.!?])\s+|\n+", text)


def _chapter_urls(url: str, chapters: str) -> list:
    m = re.search(r"/s/(\d+)/(\d+)", url)
    if not m or not chapters.strip():
        return [url]
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}/s/{m.group(1)}/"
    rng = re.match(r"\s*(\d+)\s*-\s*(\d+)\s*$", chapters)
    if rng:
        a, b = sorted((int(rng.group(1)), int(rng.group(2))))
        nums = range(a, min(b, a + 19) + 1)                    # cap at 20 chapters
    else:
        nums = ([int(x) for x in re.findall(r"\d+", chapters)] or [int(m.group(2))])[:20]
    return [base + str(n) + "/" for n in nums]


def _progression_events(text: str, character: str, at: str) -> list:
    events, sub = [], character.lower()
    for m in re.finditer(r"\[([^\[\]]{2,90})\]", text):        # LitRPG system notifications
        inner = m.group(1).strip()
        if _SYS_KEY.search(inner) or len(re.findall(r"\b[A-Z][a-z]+", inner)) >= 2:
            events.append({"at": at, "kind": "system", "text": "[" + re.sub(r"\s+", " ", inner) + "]"})
    for sent in _sentences(text):                              # prose "learned/gained …" about the character
        if sub in sent.lower() and _PROG_CUE.search(sent):
            s = re.sub(r"\s+", " ", sent).strip()
            if 12 <= len(s) <= 300:
                events.append({"at": at, "kind": "prose", "text": s})
    return events


@skill("fic_progression", "Track a character's evolution across a fanfiction — the new abilities, skills, "
       "spells, feats and levels they gain (LitRPG [Skill]/[Level Up] notifications and learned/gained prose).",
       {"url": "a chapter URL, e.g. https://www.fanfiction.net/s/<id>/1/",
        "character": "whose progression to track, e.g. O'Neill",
        "chapters": "optional chapter range to scan, e.g. 1-12 (default: just this chapter)",
        "out": "optional .json file to write the timeline to"},
       risk="read", external=True)
def fic_progression(url: str, character: str, chapters: str = "", out: str = "") -> SkillResult:
    from libra.web import parse_html

    if not character.strip():
        raise ValueError("fic_progression needs a character name")
    timeline, seen, scanned = [], set(), 0
    for i, chap_url in enumerate(_chapter_urls(url, chapters), 1):
        try:
            resp = fetcher(delay=0.3).get(chap_url)
        except Exception:
            continue
        if resp.status != 200:
            continue
        if _looks_blocked(resp):
            if scanned == 0:
                return SkillResult("fic_progression", False,
                                   "fanfiction.net returned its bot-check page instead of the chapter. Open it "
                                   "in a browser, save the HTML, and read that.")
            break
        text = _story_text(parse_html(resp.text))
        if not text.strip():
            continue
        scanned += 1
        chap_no = re.search(r"/s/\d+/(\d+)", chap_url)
        at = f"ch {chap_no.group(1)}" if (chapters.strip() and chap_no) else ""
        for e in _progression_events(text, character, at):
            key = e["text"].lower()
            if key not in seen:
                seen.add(key)
                timeline.append(e)
    if scanned == 0:
        return SkillResult("fic_progression", False,
                           f"Couldn't read any chapter text from {url} (is it a chapter page, …/s/<id>/<n>/?).")
    if not timeline:
        return SkillResult("fic_progression", False,
                           f"No progression events for “{character}” found in {scanned} chapter(s) — no "
                           "[Skill]/[Level Up] notifications or learned/gained lines naming them.")
    record = {"character": character, "url": url, "chapters_scanned": scanned, "count": len(timeline),
              "timeline": timeline}
    if out:
        with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return SkillResult("fic_progression", True, _render_progression(record, out), record)


def _render_progression(r: dict, out: str) -> str:
    lines = [f"# {r['character']} — capabilities gained",
             f"{r['count']} events across {r['chapters_scanned']} chapter(s)", ""]
    for e in r["timeline"]:
        tag = (e["at"] + " ") if e["at"] else ""
        mark = "⚙ " if e["kind"] == "system" else "• "
        lines.append(f"{mark}{tag}{e['text']}")
    lines += ["", f"Source: {r['url']}  [fanfiction.net]"]
    if out:
        lines += ["", f"[timeline written to {out}]"]
    return "\n".join(lines)


# ----------------------------------------------------------- community page
def _story_row(row) -> dict:
    title_node = row.select_one("a.stitle")
    meta_node = row.select_one(".z-padtop2") or row.select_one(".xgray")
    indent = row.select_one(".z-indent")
    summary = indent.text if indent else ""
    meta_text = meta_node.text if meta_node else ""
    if meta_text and summary.endswith(meta_text):                 # strip the meta out of the summary blob
        summary = summary[: -len(meta_text)].strip()
    return {"title": title_node.text if title_node else "", "author": _first_user_link(row),
            "summary": summary, **_parse_meta_line(meta_text)}


def _route_community(message: str):
    m = re.search(r"(https?://(?:www\.)?fanfiction\.net/community/\S+)", message, re.I)
    return {"url": m.group(1)} if m else None


def _row_matches(row: dict, query: str) -> bool:
    hay = " ".join(str(row.get(k, "")) for k in ("title", "summary", "characters", "genre", "rating")).lower()
    return all(term in hay for term in query.lower().split())


@skill("fic_community", "List (and optionally search) the stories collected in a fanfiction.net community, "
       "each with its author, summary and metadata.",
       {"url": "the community URL, e.g. https://www.fanfiction.net/community/Crossovers/1076/",
        "query": "optional words to filter the stories by (title/summary/characters/genre)",
        "limit": "most stories to return (default 50)", "out": "optional .json file to write the list to"},
       router=_route_community, risk="read", external=True)
def fic_community(url: str, query: str = "", limit: int = 50, out: str = "") -> SkillResult:
    from libra.web import parse_html

    resp = _fetch(url)
    if resp.status != 200:
        return SkillResult("fic_community", False, f"{url} returned HTTP {resp.status}.")
    if _looks_blocked(resp):
        return SkillResult("fic_community", False,
                           "fanfiction.net returned its bot-check page instead of the community (it blocks "
                           "automated fetches). Open the page in a browser, save the HTML, and read that.")
    doc = parse_html(resp.text)
    name_node = doc.select_one("#content_wrapper_inner span.xcontrast_txt") or doc.find("title")
    rows = [r for r in (_story_row(row) for row in doc.select(".z-list")) if r["title"]]
    if query.strip():
        rows = [r for r in rows if _row_matches(r, query.strip())]
    rows = rows[: max(0, int(limit))]
    if not rows:
        if query.strip():
            return SkillResult("fic_community", False, f"No stories in that community match “{query}”.")
        return SkillResult("fic_community", False,
                           f"No stories found on {url}. Make sure it's a community page "
                           "(…/community/<name>/<id>/) with a story list.")
    record = {"url": url, "community": name_node.text if name_node else "", "query": query,
              "count": len(rows), "stories": rows}
    if out:
        with open(os.path.expanduser(out), "w", encoding="utf-8") as f:
            json.dump(record, f, ensure_ascii=False, indent=2)
    return SkillResult("fic_community", True, _render_community(record, out), record)


def _render_community(r: dict, out: str) -> str:
    lines = [f"# {r['community']}" if r["community"] else "# Community", f"{r['count']} stories", ""]
    for i, s in enumerate(r["stories"], 1):
        tags = " · ".join(str(s[k]) for k in ("rating", "genre", "words", "status") if s.get(k))
        head = f"{i}. **{s['title']}**" + (f" — {s['author']}" if s["author"] else "")
        lines.append(head + (f"  ({tags})" if tags else ""))
        if s["summary"]:
            lines.append(f"   {s['summary'][:200]}")
    lines += ["", f"Source: {r['url']}  [fanfiction.net]"]
    if out:
        lines += ["", f"[list written to {out}]"]
    return "\n".join(lines)
