---
name: my-skill
description: What this skill does, and when to use it (Claude reads this to decide when to load it)
disable-model-invocation: true
allowed-tools: Read Grep
---

Your skill instructions here...

## Available scripts

- **`scripts/example.sh`**: greets NAME (an example of an agent-friendly script). `--json` for structured output, `--help` for usage

## Workflow

1. Run the script:
   ```bash
   bash scripts/example.sh --json "$NAME"
   ```
2. Exit code `2` means the arguments were wrong. The error says what was expected.

<!--
A starter for a Claude Code skill (https://code.claude.com/docs/en/skills).
Layout (only SKILL.md is required):

  my-skill/
  ├── SKILL.md          metadata + instructions
  ├── scripts/          code the skill runs (see scripts/example.sh)
  ├── references/       longer documentation it links to
  └── assets/           templates and other files it uses

Copy this folder to .claude/skills/<name>/ in this repository, or to ~/.claude/skills/<name>/
on your own computer. The folder name, or `name` above, becomes the command: /my-skill.

Frontmatter:
  description               When to use it. Specific words help Claude pick it at the right moment.
  disable-model-invocation  true: only a person starts it (anything that publishes, deploys or deletes).
                            Leave it out to let Claude use the skill when it's relevant.
  allowed-tools             Tools the skill may use without asking, e.g. "Read Grep" for a read-only
                            skill. Leave it out to use the normal permission rules.
  context: fork             Run it in a separate context (long logs stay out of the conversation).
  argument-hint             What to type after the command, e.g. "[version]". The text arrives as $ARGUMENTS.

Scripts: refer to them by paths relative to the skill folder (bash scripts/example.sh), and list them in
an "Available scripts" section so Claude knows they exist. Write them for an agent: no prompts, --help,
clear errors, structured output on stdout, diagnostics on stderr, documented exit codes, --dry-run for
changes. scripts/example.sh shows each of these. State what they need in a `compatibility` field
(e.g. "Requires Python 3.9+").

Body: plain instructions. A line like !`git status --short` runs before Claude reads the skill
and is replaced by its output, so the instructions start from the real state.

Libra's own skills are in .claude/skills/. See verify, release and deploy for examples, and
roll-dice for a skill with a script and a reference file.
(These are Claude Code skills. Libra's skills, its tools, are made from templates/plugin.py.)
-->
