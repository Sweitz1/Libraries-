"""Send a short notification: ntfy topics, Discord webhooks, or any URL that accepts a POST."""
from __future__ import annotations

import json


def send(url: str, title: str, body: str, allow_private: bool = False) -> bool:
    """POST ``title`` + ``body`` to ``url``. Discord webhooks get JSON; everything else plain text with a Title header."""
    from .web import FetchError, Fetcher

    fetcher = Fetcher(allow_private=allow_private, respect_robots=False, timeout=15)
    try:
        if "discord.com/api/webhooks/" in url or "discordapp.com/api/webhooks/" in url:
            content = f"**{title}**\n{body}"[:1990]
            fetcher.request("POST", url, {"Content-Type": "application/json"},
                            json.dumps({"content": content, "allowed_mentions": {"parse": []}}).encode())
        else:
            safe_title = title[:180].encode("ascii", "replace").decode()
            fetcher.request("POST", url, {"Title": safe_title, "Content-Type": "text/plain; charset=utf-8"},
                            body.encode("utf-8"))
        return True
    except (FetchError, OSError):
        return False
