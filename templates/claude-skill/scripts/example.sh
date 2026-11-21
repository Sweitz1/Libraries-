#!/usr/bin/env bash
# Scripts hold the exact commands a skill runs, so Claude doesn't retype them from prose.
# SKILL.md tells Claude when to run this, e.g.: bash <skill folder>/scripts/example.sh <arg>
set -euo pipefail
echo "example script got: ${1:-nothing}"
