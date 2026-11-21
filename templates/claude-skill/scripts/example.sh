#!/usr/bin/env bash
# Usage: scripts/example.sh [--json] [--dry-run] NAME
#
# An example of a script written for an agent to run (https://agentskills.io):
#   - no prompts: all input comes from arguments, environment variables or stdin
#   - --help explains it, briefly, with examples
#   - errors say what was received, what was expected, and what to try
#   - data goes to stdout (here as JSON with --json), progress and warnings to stderr
#   - distinct exit codes, documented here
#   - --dry-run for anything that changes state; safe to run twice
#
# Options:
#   --json      print {"name": ..., "greeting": ...} instead of text
#   --dry-run   say what would happen, change nothing
#   -h, --help
#
# Exit codes: 0 done, 2 invalid arguments.
#
# Examples:
#   scripts/example.sh Ada
#   scripts/example.sh --json Ada
set -euo pipefail
json=0 dry=0 name=""
while [ $# -gt 0 ]; do
  case "$1" in
    --json) json=1; shift ;;
    --dry-run) dry=1; shift ;;
    -h|--help) sed -n '2,/^set -euo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) echo "Error: unknown option '$1'. Options: --json, --dry-run, --help." >&2; exit 2 ;;
    *) name="$1"; shift ;;
  esac
done
[ -n "$name" ] || { echo "Error: NAME is required. Usage: scripts/example.sh [--json] NAME" >&2; exit 2; }
[ "$dry" = 1 ] && { echo "would greet $name" >&2; exit 0; }
if [ "$json" = 1 ]; then printf '{"name": "%s", "greeting": "hello, %s"}\n' "$name" "$name"; else echo "hello, $name"; fi
