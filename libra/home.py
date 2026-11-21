"""Where Libra keeps its settings: ``~/.libra`` (override with ``LIBRA_HOME``)."""
from __future__ import annotations

import json
import os


def home() -> str:
    path = os.environ.get("LIBRA_HOME") or os.path.join(os.path.expanduser("~"), ".libra")
    os.makedirs(path, exist_ok=True)
    return path


def path(*parts: str) -> str:
    return os.path.join(home(), *parts)


def read_json(name: str, default):
    p = path(name)
    if not os.path.exists(p):
        return default
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def write_json(name: str, value) -> None:
    p = path(name)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p + ".tmp", "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
    os.replace(p + ".tmp", p)
