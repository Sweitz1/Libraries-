#!/usr/bin/env python3
"""Roll dice in standard notation. Every roll is uniform (secrets.randbelow).

Usage: scripts/roll.py [--json] SPEC [SPEC ...]

SPEC is dice notation: d20, 2d6, 3d8+4, 4d6-1 (default: d6).

Options:
  --json   one JSON object per roll on stdout: {"spec", "rolls", "modifier", "total"}
  -h, --help

Examples:
  scripts/roll.py 2d6
  scripts/roll.py --json d20 3d8+4

Exit codes: 0 rolled, 2 invalid notation or arguments.
"""
import json
import re
import secrets
import sys

DICE = re.compile(r"^(\d*)d(\d+)([+-]\d+)?$", re.I)
MAX_DICE, MAX_SIDES = 1000, 1_000_000


class NotationError(ValueError):
    pass


def roll(spec: str) -> dict:
    m = DICE.match(spec.strip())
    if not m:
        raise NotationError(f"Error: {spec!r} isn't dice notation.\n"
                            "       Expected [COUNT]dSIDES[+/-MODIFIER], e.g. d20, 2d6, 3d8+4.")
    count, sides, mod = int(m.group(1) or 1), int(m.group(2)), int(m.group(3) or 0)
    if not 1 <= count <= MAX_DICE or not 2 <= sides <= MAX_SIDES:
        raise NotationError(f"Error: {spec!r} is out of range.\n"
                            f"       Use 1 to {MAX_DICE} dice with 2 to {MAX_SIDES:,} sides.")
    rolls = [secrets.randbelow(sides) + 1 for _ in range(count)]
    return {"spec": spec, "rolls": rolls, "modifier": mod, "total": sum(rolls) + mod}


def text(r: dict) -> str:
    rolls, mod = r["rolls"], r["modifier"]
    if len(rolls) == 1 and not mod:
        return f"{r['spec']}: {r['total']}"
    shown = " + ".join(map(str, rolls))
    extra = f" {'+' if mod > 0 else '-'} {abs(mod)}" if mod else ""
    return f"{r['spec']}: {shown}{extra} = {r['total']}"


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__.strip())
        return 0
    as_json = "--json" in argv
    specs = [a for a in argv if a != "--json"]
    unknown = [a for a in specs if a.startswith("-")]
    if unknown:
        print(f"Error: unknown option {unknown[0]!r}. Options: --json, --help.", file=sys.stderr)
        return 2
    try:
        results = [roll(s) for s in specs or ["d6"]]  # validate everything before printing anything
    except NotationError as exc:
        print(exc, file=sys.stderr)
        return 2
    for r in results:
        print(json.dumps(r) if as_json else text(r))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
