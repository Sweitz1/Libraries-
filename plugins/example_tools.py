"""Example Libra plugin. Load it with:  libra chat --plugins plugins/

Each @skill function becomes a tool you can run as /name in chat, with
`libra skill run name ...`, or over HTTP at POST /v1/skills/name.
"""
import re

from libra.skills import skill


@skill("word_count", "Count words, characters and sentences in some text.", {"text": "the text to measure"}, risk="read")
def word_count(text: str) -> dict:
    return {
        "words": len(text.split()),
        "characters": len(text),
        "sentences": len([s for s in re.split(r"[.!?]+", text) if s.strip()]),
    }


def _route_headlines(message: str):
    m = re.search(r"headlines? (?:on|from|at) (https?://\S+)", message, re.I)
    return {"url": m.group(1)} if m else None


@skill("headlines", "List the headings of a web page.", {"url": "page to read"}, router=_route_headlines, risk="read")
def headlines(url: str) -> str:
    from libra.skills import fetcher
    from libra.web import scrape

    page = scrape(url, fetcher=fetcher())
    return "\n".join(f"{'  ' * (h['level'] - 1)}- {h['text']}" for h in page["headings"]) or "(no headings)"
