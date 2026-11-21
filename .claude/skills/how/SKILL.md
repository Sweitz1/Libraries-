---
name: how
description: Explains what Libra's code does now. It traces the execution path from an entry point (a CLI command, an HTTP route, an app tab, a timer) through the code, and names who owns each piece of state on disk. Read-only. Use for "how does X work", "what happens when", or "where is X handled".
argument-hint: "<what to explain>"
---

Explain how `$ARGUMENTS` works, as the code does it today. Read code, don't change it, and don't guess: every claim points at a `file:line` you read.

## Find the entry point

| Starts from | Look in |
|---|---|
| a command (`libra …`) | `libra/cli.py`: the `cmd_<name>` function and its `add_parser` block |
| an HTTP request | `libra/server.py`: `do_GET`/`do_POST`, then the `_handler` method for the path |
| the phone/web app | `libra/webui/app.js` (calls the API), then the server route |
| the Android app | `android/app/src/main/java/ai/libra/app/` (WebView, pairing, `CheckService` polling) |
| a timer or service | `deploy/install-debian.sh`: the systemd units, which run `libra auto run` and `libra agent run-due` |
| a skill | `libra/skills.py` `SkillRegistry.run` (trust check, audit), then the skill's function |

## State and who owns it

Everything lives under `~/.libra` (or `LIBRA_HOME`; `/var/lib/libra/home` on the Debian service).

| File | Owner |
|---|---|
| `trust.json`, `audit.jsonl` | `libra/trust.py` |
| `devices.json`, `server-token` | `libra/devices.py`, `libra/cli.py` |
| `memory.jsonl` | `libra/memory.py` |
| `agent/goals.json`, `agent/runs.jsonl`, `agent/procedures.json`, `agent/settings.json` | `libra/agent.py` |
| `jobs/<id>/job.json`, `events.jsonl`, `lock` | `libra/jobs.py` |
| `eval/history.jsonl` | `libra/evaluate.py` |
| `catalog.db`, `mcp-stderr.log` | `libra/catalog.py`, `libra/mcp.py` |
| `translate.db` | `libra/translate.py` |
| `discord.json`, `discord-token` | `libra/discord.py` |
| checkpoints: `CURRENT`, `versions/<name>/` | `libra/checkpoint.py` |

Unattended training keeps its state next to its dataset, in `<dataset>/.auto/`.

## Answer

1. **One sentence first:** what happens, end to end.
2. **The path**, step by step, each with `file:line`: entry → checks (auth, trust, limits) → work → state written → response.
3. **The state** it reads and writes, from the table above.
4. **When it helps, a short Mermaid `sequenceDiagram`** of the parts involved.
5. **What it doesn't do**, if the question assumed something the code doesn't do.

For *why* the code is built this way, use the `why` skill.
