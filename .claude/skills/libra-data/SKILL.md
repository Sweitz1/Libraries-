---
name: libra-data
description: Validates Libra's configuration files (libra auto training configs, eval and planner suites, trust rules, saved goals, plugins) and processes input data (notes, docs, chat exports) into a training dataset. Use before running or committing a config, before training on new text, or when a Libra config "isn't doing anything".
compatibility: Requires bash and Python 3.9+, run from a Libra checkout (the scripts use its own loaders). No network access.
---

## Available scripts

- **`scripts/validate.sh`**: validates configuration files.
  - **What it checks:** training configs, benchmark suites, trust rules, saved goals and plugins, each with Libra's own loaders. It detects the kind from the file name and contents; `--kind` overrides that.
  - **Typos:** unknown keys are flagged. A misspelled setting is otherwise silently ignored, and the default used instead.
  - **Plugins:** they are parsed, never run.
  - **Output:** one JSON line per file (`file`, `kind`, `valid`, `problems`, `warnings`), then a summary.
  - **Exit codes:** `0` all valid, `1` a file is invalid, `2` bad arguments, `3` a file doesn't exist.
- **`scripts/process.py`**: processes input data.
  - **Input:** files or folders (`.txt`, `.md`, `.html`, `.json`/`.jsonl` text or chats including ChatGPT exports, `.csv`/`.tsv`).
  - **What it does:** cleans them, skips short pieces, and removes duplicates. It writes `text.jsonl`, `chat.jsonl` and `stats.json` to `--out`, which is ready for `libra train`.
  - **Output:** one JSON object with the counts and a suggested `libra train` command.
  - **Exit codes:** `0` done, `1` no usable text, `2` bad arguments, `3` an input doesn't exist.

## Workflow

1. Validate before using a config:
   ```bash
   bash scripts/validate.sh ~/libra-auto.json
   ```
   Fix every `problems` entry. Read the `warnings` to the user: they don't block, but a placeholder `example.com` source or a misspelled key usually explains "it ran but did nothing".
2. Preview the data first, then write it:
   ```bash
   python3 scripts/process.py --dry-run ~/notes chats.json
   python3 scripts/process.py --out datasets/notes ~/notes chats.json
   ```
   Re-running with the same inputs writes the same files. Exit `1` means nothing usable was found: try a lower `--min-chars`, or check the paths.
3. Train with the `suggestion.command` it prints (replace the output folder if you like), or hand the dataset to `libra auto` as a `{"files": …}` source.

## Notes

- Both scripts run offline. To collect from the web, use `libra data --url/--crawl` or `libra auto`, which follow robots.txt and the private-address guard.
- `validate.sh` never runs a plugin's code. To check one really loads, use `libra skill list --plugins <folder>`, and only for plugins you trust: they run with your permissions.
