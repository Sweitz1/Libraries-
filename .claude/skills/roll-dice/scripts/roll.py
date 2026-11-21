#!/usr/bin/env python3
"""Roll dice in standard notation: d20, 2d6, 3d8+4, 4d6-1. Every roll is uniform (secrets.randbelow)."""
import re
import secrets
import sys

DICE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$", re.I)


def roll(spec: str) -> str:
    m = DICE.match(spec.strip())
    if not m:
        raise ValueError(f"{spec!r} isn't dice notation (try d20, 2d6 or 3d8+4)")
    count, sides, mod = int(m.group(1) or 1), int(m.group(2)), int(m.group(3) or 0)
    if not 1 <= count <= 1000 or not 2 <= sides <= 1_000_000:
        raise ValueError("use 1 to 1000 dice with 2 to 1,000,000 sides")
    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
    total = sum(rolls) + mod
    shown = " + ".join(map(str, rolls)) if count > 1 else str(rolls[0])
    extra = f" {'+' if mod > 0 else '-'} {abs(mod)}" if mod else ""
    return f"{spec}: {shown}{extra} = {total}" if count > 1 or mod else f"{spec}: {total}"


if __name__ == "__main__":
    try:
        for s in sys.argv[1:] or ["d6"]:
            print(roll(s))
    except ValueError as exc:
        sys.exit(str(exc))
