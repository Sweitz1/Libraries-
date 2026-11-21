#!/usr/bin/env bash
# Usage: scripts/deploy.sh --target USER@HOST [--version REF] [--options "INSTALLER OPTIONS"] [--dry-run]
#
# Deploys Libra to a Debian server: runs the tests here, checks out REF in the server's checkout,
# runs deploy/install-debian.sh there, checks that it came up, and rolls back if it didn't.
# Never prompts: SSH runs in batch mode, and sudo must not need a password (sudo -n).
#
# Options:
#   --target USER@HOST   the server (required)
#   --version REF        tag or commit to deploy (default: origin/main)
#   --options "..."      installer options, e.g. "--tailscale --port 8080". The installer doesn't
#                        remember them: pass the same ones as the first install, or the upgrade
#                        changes how the server is reached
#   --port N             port for the health check (default: 8000, or --port from --options)
#   --dir PATH           the checkout on the server (default: ~/Libraries-; cloned if missing)
#   --repo URL           where to clone from (default: https://github.com/Sweitz1/Libraries-.git)
#   --skip-tests         don't run the tests here first
#   --dry-run            check the server can be reached and show what would run; change nothing
#   -h, --help
#
# Environment: LIBRA_DEPLOY_SSH is the ssh command to use (default: ssh).
#
# Output: one JSON object per step on stdout: {"step", "status": "ok|failed|skipped|planned", "detail"}.
# Progress goes to stderr.
#
# Exit codes:
#   0 deployed and healthy (or the dry run found nothing wrong)
#   2 invalid arguments         3 can't reach the server     4 the tests failed
#   5 couldn't prepare the server's checkout                 6 sudo needs a password (run it yourself)
#   7 the installer failed      8 unhealthy, rolled back     9 unhealthy, and the rollback failed too
#
# Examples:
#   scripts/deploy.sh --target pi@atlas.local --version v0.15.0 --options "--tailscale" --dry-run
#   scripts/deploy.sh --target pi@atlas.local --version v0.15.0 --options "--tailscale"
set -uo pipefail

target="" ref="origin/main" options="" port="" dir="~/Libraries-" repo="https://github.com/Sweitz1/Libraries-.git"
skip_tests=0 dry=0
SSH="${LIBRA_DEPLOY_SSH:-ssh}"
usage() { sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
arg() { [ -n "${2:-}" ] || { printf 'Error: %s needs a value.\nUsage: scripts/deploy.sh --target USER@HOST [--version REF] [--options "..."] [--dry-run]   (--help for all options)\n' "$1" >&2; exit 2; }; }
while [ $# -gt 0 ]; do
  case "$1" in
    --target) arg "$1" "${2:-}"; target="$2"; shift 2 ;;
    --version) arg "$1" "${2:-}"; ref="$2"; shift 2 ;;
    --options) options="${2:-}"; shift 2 ;;
    --port) arg "$1" "${2:-}"; port="$2"; shift 2 ;;
    --dir) arg "$1" "${2:-}"; dir="$2"; shift 2 ;;
    --repo) arg "$1" "${2:-}"; repo="$2"; shift 2 ;;
    --skip-tests) skip_tests=1; shift ;;
    --dry-run) dry=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Error: unknown argument '$1'. Run with --help for the options." >&2; exit 2 ;;
  esac
done
[ -n "$target" ] || { printf 'Error: --target is required: the server, as USER@HOST.\nUsage: scripts/deploy.sh --target pi@atlas.local --version v0.15.0 --options "--tailscale" [--dry-run]\n' >&2; exit 2; }
[[ "$ref" =~ ^[A-Za-z0-9._/-]+$ ]] || { echo "Error: --version must be a tag, branch or commit. Received: '$ref'" >&2; exit 2; }
[[ "$options" =~ ^[A-Za-z0-9\ ._:=/@-]*$ ]] || { echo "Error: --options may only contain installer flags and values. Received: '$options'" >&2; exit 2; }
if [ -z "$port" ]; then port="$(sed -n 's/.*--port[ =]\([0-9]*\).*/\1/p' <<< "$options")"; port="${port:-8000}"; fi
[[ "$port" =~ ^[0-9]+$ ]] || { echo "Error: --port must be a number. Received: '$port'" >&2; exit 2; }

step() { python3 -c 'import json,sys; print(json.dumps({"step": sys.argv[1], "status": sys.argv[2], "detail": sys.argv[3]}))' "$1" "$2" "${3:-}"; }
say() { echo "deploy: $*" >&2; }
remote() { "$SSH" -o BatchMode=yes -o ConnectTimeout=15 "$target" "$@"; }
here="$(cd "$(dirname "$0")/../../../.." && pwd)"   # this file is .claude/skills/deploy/scripts/deploy.sh
[ -f "$here/libra/__init__.py" ] || here=""

# 1. the server answers over SSH
say "checking $target …"
if ! remote true 2>/tmp/libra-deploy-ssh.err; then
  step reach failed "can't reach $target over SSH: $(tail -1 /tmp/libra-deploy-ssh.err). Run the steps on the server yourself, or set up key-based SSH."
  exit 3
fi
current="$(remote "/opt/libra/venv/bin/python -c 'import libra; print(libra.__version__)' 2>/dev/null || echo 'none installed'")"
step reach ok "running version: $current"

# 2. the tests, here
if [ "$skip_tests" = 1 ] || [ "$dry" = 1 ]; then
  step tests skipped "$([ "$dry" = 1 ] && echo "dry run" || echo "--skip-tests")"
elif [ -z "$here" ]; then
  step tests failed "not inside a Libra checkout, so the tests can't run here (use --skip-tests to deploy anyway)"; exit 4
else
  say "running the tests …"
  if out="$(bash "$here/.claude/skills/verify/scripts/verify.sh" --only python,smoke 2>/dev/null)"; then
    step tests ok "$(tail -1 <<< "$out")"
  else
    step tests failed "$(grep '"failed"' <<< "$out" | head -1)"; exit 4
  fi
fi

prepare="set -e; d=$dir; if [ ! -d \"\$d/.git\" ]; then git clone -q $repo \"\$d\"; echo none; else git -C \"\$d\" rev-parse HEAD; fi"
checkout() { echo "set -e; cd $dir && git fetch -q --tags origin && git checkout -q --detach $1 && git log --oneline -1"; }
install="cd $dir && sudo -n ./deploy/install-debian.sh $options"
health="for i in \$(seq 30); do python3 -c 'import urllib.request,json,sys; r=json.load(urllib.request.urlopen(\"http://127.0.0.1:$port/health\", timeout=2)); sys.exit(r.get(\"status\") != \"ok\")' 2>/dev/null && { echo healthy; exit 0; }; sleep 1; done; exit 1"

if [ "$dry" = 1 ]; then
  if remote "sudo -n true" 2>/dev/null; then step sudo ok "sudo works without a password"; else step sudo failed "sudo needs a password on $target: the deploy would stop at the install step (exit 6)"; fi
  step checkout planned "$(checkout "$ref")"
  step install planned "$install"
  step health planned "GET http://127.0.0.1:$port/health on the server must answer {\"status\": \"ok\"}"
  exit 0
fi

# 3. sudo works without a password (checked before anything on the server changes)
remote "sudo -n true" 2>/dev/null || { step sudo failed "sudo needs a password on $target, so nothing was changed. Run the steps there yourself, or allow passwordless sudo for the installer. Install command: $install"; exit 6; }
step sudo ok "sudo works without a password"

# 4. the server's checkout, at the new version (remember the old one for a rollback)
say "preparing $dir on $target …"
previous="$(remote "$prepare" 2>/tmp/libra-deploy.err | tail -1)" || { step prepare failed "$(tail -2 /tmp/libra-deploy.err)"; exit 5; }
now="$(remote "$(checkout "$ref")" 2>/tmp/libra-deploy.err)" || { step prepare failed "couldn't check out $ref: $(tail -1 /tmp/libra-deploy.err)"; exit 5; }
step prepare ok "now at $now (was ${previous:-none})"

# 5. install
say "running the installer …"
if ! remote "$install" > /tmp/libra-deploy-install.log 2>&1; then
  step install failed "the installer stopped: $(tail -3 /tmp/libra-deploy-install.log | tr '\n' ' ') (full log: /tmp/libra-deploy-install.log)"; exit 7
fi
step install ok "options: ${options:-(none)}"

# 6. it came up, or roll back
say "checking the server came up …"
if remote "$health" >/dev/null 2>&1; then
  step health ok "http://127.0.0.1:$port/health answers ok"
  exit 0
fi
step health failed "no healthy answer from port $port within 30 s"
if [ "$previous" = "none" ] || [ -z "$previous" ]; then
  step rollback skipped "this was the first install; there is nothing to roll back to"; exit 9
fi
say "rolling back to $previous …"
if remote "$(checkout "$previous")" >/dev/null 2>&1 && remote "$install" >> /tmp/libra-deploy-install.log 2>&1 \
    && remote "$health" >/dev/null 2>&1; then
  step rollback ok "back on $previous and healthy"; exit 8
fi
step rollback failed "still unhealthy after rolling back to $previous; look at: journalctl -u libra -n 50"
exit 9
