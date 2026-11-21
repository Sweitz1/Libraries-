#!/usr/bin/env python3
"""Process input data into a Libra training dataset (the same cleaning as `libra data`).

Usage: scripts/process.py --out DIR [--min-chars N] [--dry-run] INPUT [INPUT ...]

INPUT is a file or a folder of them: .txt, .md, .html, .json/.jsonl (text or chat records,
including ChatGPT exports), .csv/.tsv. Text is cleaned, short pieces are skipped, and exact
duplicates are removed.

Options:
  --out DIR        where to write text.jsonl, chat.jsonl and stats.json (required unless --dry-run)
  --min-chars N    skip pieces shorter than N characters (default: 40)
  --dry-run        read and count, but write nothing
  -h, --help

Output: one JSON object on stdout:
  {"inputs", "documents", "conversations", "characters", "estimated_tokens", "skipped",
   "duplicates_removed", "suggestion": {...}, "out", "written"}
Progress goes to stderr. Running it again with the same inputs writes the same files.

Exit codes: 0 done, 1 no usable text in the inputs, 2 invalid arguments, 3 an input doesn't exist.

Examples:
  scripts/process.py --dry-run ~/notes
  scripts/process.py --out datasets/notes ~/notes chats.json
"""
import json
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))  # .claude/skills/libra-data/scripts
sys.path.insert(0, ROOT)

USAGE = "Usage: scripts/process.py --out DIR [--min-chars N] [--dry-run] INPUT ...   (--help for details)"


def fail(code: int, message: str) -> int:
    print(f"Error: {message}\n{USAGE}", file=sys.stderr)
    return code


def main(argv: list) -> int:
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__.strip())
        return 0
    out, min_chars, dry, inputs = None, 40, False, []
    i = 0
    while i < len(argv):
        a = argv[i]
        if a in ("--out", "--min-chars"):
            if i + 1 >= len(argv):
                return fail(2, f"{a} needs a value.")
            if a == "--out":
                out = argv[i + 1]
            else:
                try:
                    min_chars = int(argv[i + 1])
                    assert min_chars >= 0
                except (ValueError, AssertionError):
                    return fail(2, f"--min-chars must be a whole number of characters. Received: {argv[i + 1]!r}")
            i += 2
        elif a == "--dry-run":
            dry, i = True, i + 1
        elif a.startswith("-"):
            return fail(2, f"unknown option {a!r}. Options: --out, --min-chars, --dry-run, --help.")
        else:
            inputs.append(a)
            i += 1
    if not inputs:
        return fail(2, "give at least one INPUT file or folder.")
    if out is None and not dry:
        return fail(2, "--out DIR is required (or use --dry-run to only count).")
    missing = [p for p in inputs if not os.path.exists(os.path.expanduser(p))]
    if missing:
        print(f"Error: no such file or folder: {missing[0]!r}", file=sys.stderr)
        return 3

    from libra.dataset import Dataset

    ds = Dataset(min_chars=min_chars)
    log = lambda m: print(m, file=sys.stderr)  # noqa: E731
    for p in inputs:
        ds.add_path(os.path.expanduser(p), log=log)
    if not ds.texts and not ds.chats:
        print(json.dumps({"inputs": inputs, **ds.stats(), "out": out, "written": False}))
        print("Error: no usable text found in the inputs (everything was too short, a duplicate, or an "
              "unsupported type). Try a lower --min-chars, or check the paths.", file=sys.stderr)
        return 1
    if dry:
        stats = ds.stats()
    else:
        stats = ds.save(out)
        log(f"wrote {out}")
    print(json.dumps({"inputs": inputs, **stats, "out": out, "written": not dry}))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
