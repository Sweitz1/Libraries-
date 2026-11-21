#!/usr/bin/env bash
# Usage: scripts/verify.sh [--only CHECKS] [--all] [--base REF] [--logs DIR]
#
# Runs Libra's checks for the parts that changed, from the repository root, without prompts.
#
# Checks: python, smoke, java, android, xml, workflows, docs
#   python     unit tests (python -m unittest discover -s tests)
#   smoke      end-to-end command-line checks (.github/smoke.sh)
#   java       clients/java with Maven, including the live test against a real `libra serve`
#   android    the app's pure-Java unit tests with JUnit (the full Android build runs in CI only)
#   xml        every Android XML file parses
#   workflows  every GitHub workflow is valid YAML
#   docs       relative Markdown links resolve; versions agree (scripts/release.sh --check)
#
# Options:
#   --only a,b   run just these checks
#   --all        run every check
#   --base REF   compare with REF to find changed files (default: origin/main)
#   --logs DIR   where full logs go (default: $TMPDIR/libra-verify)
#   -h, --help
#
# Without --only/--all, the checks are chosen from the files changed since --base, plus
# uncommitted ones. Any code change runs python and smoke.
#
# Output: one JSON object per check on stdout:
#   {"check": "python", "status": "passed|failed|skipped", "seconds": 41, "detail": "...", "log": "..."}
# then a summary object {"summary": {"passed": n, "failed": n, "skipped": n}}.
# Progress goes to stderr. A check is "skipped" when its tool is missing (the detail says which).
#
# Exit codes: 0 every check that ran passed, 1 a check failed, 2 invalid arguments, 3 not in a Libra checkout.
# Without git (or with a git that refuses the folder), the checks to run can't be detected: use --only or --all.
#
# Examples:
#   scripts/verify.sh
#   scripts/verify.sh --only python,docs
#   scripts/verify.sh --all --logs /tmp/v
set -uo pipefail

ALL="python smoke java android xml workflows docs"
only="" all=0 base="origin/main" logs="${TMPDIR:-/tmp}/libra-verify"
usage() { sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; }
while [ $# -gt 0 ]; do
  case "$1" in
    --only) only="${2:-}"; shift 2 || { echo "Error: --only needs a list, e.g. --only python,docs" >&2; exit 2; } ;;
    --all) all=1; shift ;;
    --base) base="${2:-}"; shift 2 || { echo "Error: --base needs a git ref" >&2; exit 2; } ;;
    --logs) logs="${2:-}"; shift 2 || { echo "Error: --logs needs a directory" >&2; exit 2; } ;;
    -h|--help) usage; exit 0 ;;
    *) printf "Error: unknown argument '%s'.\nUsage: scripts/verify.sh [--only python,docs] [--all] [--base REF]   (--help for details)\n" "$1" >&2; exit 2 ;;
  esac
done

root="$(cd "$(dirname "$0")/../../../.." && pwd)"   # this file is .claude/skills/verify/scripts/verify.sh
cd "$root"
[ -f libra/__init__.py ] && [ -d tests ] || { echo "Error: $root isn't a Libra checkout (no libra/ and tests/)" >&2; exit 3; }
mkdir -p "$logs"

# --- which checks
if [ "$all" = 1 ]; then
  checks="$ALL"
elif [ -n "$only" ]; then
  checks="$(echo "$only" | tr ',' ' ')"
  for c in $checks; do
    case " $ALL " in *" $c "*) ;; *) echo "Error: unknown check '$c'. Checks: ${ALL// /, }" >&2; exit 2 ;; esac
  done
else
  changed="$( { git diff --name-only "$base"...HEAD 2>/dev/null; git diff --name-only HEAD 2>/dev/null;
               git ls-files --others --exclude-standard; } | sort -u)"
  checks=""
  add() { case " $checks " in *" $1 "*) ;; *) checks="$checks $1" ;; esac; }
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    case "$f" in
      libra/*|tests/*|pyproject.toml|.github/smoke.sh|plugins/*|data/*|examples/*) add python; add smoke ;;
    esac
    case "$f" in clients/java/*) add java ;; esac
    case "$f" in android/*.java) add android ;; esac
    case "$f" in android/*.xml) add xml ;; esac
    case "$f" in .github/workflows/*) add workflows ;; esac
    case "$f" in *.md|scripts/release.sh|android/app/build.gradle|clients/java/pom.xml) add docs ;; esac
  done <<< "$changed"
  [ -z "${checks// /}" ] && checks="python"   # nothing detected: at least the unit tests
fi

passed=0 failed=0 skipped=0
emit() {  # check status seconds detail log
  python3 -c 'import json,sys; print(json.dumps({"check": sys.argv[1], "status": sys.argv[2], "seconds": int(sys.argv[3]), "detail": sys.argv[4], "log": sys.argv[5]}))' "$@"
  case "$2" in passed) passed=$((passed+1)) ;; failed) failed=$((failed+1)) ;; *) skipped=$((skipped+1)) ;; esac
}
need() { command -v "$1" >/dev/null 2>&1; }

run_check() {
  local c="$1" log="$logs/$1.log" t0=$SECONDS detail="" ok=0
  echo "verify: $c …" >&2
  case "$c" in
    python)
      if ! python3 -c "import libra" 2>/dev/null; then pip install -q -e . >"$log" 2>&1; fi
      python3 -m unittest discover -s tests >"$log" 2>&1 && ok=1
      detail="$(grep -E '^(Ran |OK|FAILED)' "$log" | tr '\n' ' ')"
      [ "$ok" = 1 ] || detail="$detail; first failure: $(grep -m1 -E '^(FAIL|ERROR):' "$log")" ;;
    smoke)
      need libra || pip install -q -e . >/dev/null 2>&1
      bash .github/smoke.sh >"$log" 2>&1 && { ok=1; detail="all commands succeeded"; } \
        || detail="failed at: $(grep -E '^\+ ' "$log" | tail -1 | cut -c3-)" ;;
    java)
      if ! need mvn || ! need java; then emit java skipped 0 "needs Maven and a JDK 11+" ""; return; fi
      local home port=8791; home="$(mktemp -d)"
      LIBRA_HOME="$home" nohup libra serve --host 127.0.0.1 --port $port --token verify-token >"$home/serve.log" 2>&1 &
      local pid=$!
      for _ in $(seq 30); do python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:$port/health', timeout=1)" 2>/dev/null && break; sleep 0.5; done
      (cd clients/java && LIBRA_TEST_URL="http://127.0.0.1:$port" LIBRA_TEST_TOKEN=verify-token mvn -q -B verify) >"$log" 2>&1 && ok=1
      kill "$pid" 2>/dev/null
      detail="$(cat clients/java/target/surefire-reports/*.xml 2>/dev/null | grep -o 'tests="[0-9]*" errors="[0-9]*" skipped="[0-9]*" failures="[0-9]*"' \
        | awk -F'"' '{t+=$2; e+=$4; s+=$6; f+=$8} END {printf "%d tests, %d failed, %d errors, %d skipped", t, f, e, s}')" ;;
    android)
      if ! need javac; then emit android skipped 0 "needs a JDK (javac)" ""; return; fi
      local cache="${XDG_CACHE_HOME:-$HOME/.cache}/libra-verify" out; out="$(mktemp -d)"
      mkdir -p "$cache"
      for jar in junit/junit/4.13.2/junit-4.13.2.jar org/hamcrest/hamcrest-core/1.3/hamcrest-core-1.3.jar; do
        [ -f "$cache/$(basename "$jar")" ] || curl -sSf -o "$cache/$(basename "$jar")" "https://repo1.maven.org/maven2/$jar" 2>>"$log" \
          || { emit android skipped 0 "couldn't download JUnit from Maven Central" "$log"; return; }
      done
      local cp="$cache/junit-4.13.2.jar:$cache/hamcrest-core-1.3.jar"
      { javac -d "$out" -cp "$cp" android/app/src/main/java/ai/libra/app/PairLink.java android/app/src/main/java/ai/libra/app/Updates.java \
          android/app/src/test/java/ai/libra/app/*.java \
        && java -cp "$out:$cp" org.junit.runner.JUnitCore ai.libra.app.PairLinkTest ai.libra.app.UpdatesTest; } >"$log" 2>&1 && ok=1
      detail="$(grep -E '^(OK|Tests run)' "$log" | tail -1) (the full build and lint run in CI)" ;;
    xml)
      python3 - >"$log" 2>&1 <<'PY' && ok=1
import glob, xml.dom.minidom
bad = []
for f in glob.glob("android/**/*.xml", recursive=True):
    try:
        xml.dom.minidom.parse(f)
    except Exception as e:
        bad.append(f"{f}: {e}")
print("\n".join(bad) or "all XML files parse")
raise SystemExit(1 if bad else 0)
PY
      detail="$(head -3 "$log" | tr '\n' ' ')" ;;
    workflows)
      if ! python3 -c "import yaml" 2>/dev/null; then emit workflows skipped 0 "needs PyYAML (pip install pyyaml)" ""; return; fi
      python3 -c 'import glob,yaml; fs=glob.glob(".github/workflows/*.yml"); [yaml.safe_load(open(f)) for f in fs]; print(len(fs), "workflows parse")' >"$log" 2>&1 && ok=1
      detail="$(tail -1 "$log")" ;;
    docs)
      python3 - >"$log" 2>&1 <<'PY' && ok=1
import glob, os, re
bad, n = [], 0
files = [f for f in glob.glob("**/*.md", recursive=True) + glob.glob(".claude/**/*.md", recursive=True)
         if "/target/" not in f and "egg-info" not in f and "node_modules" not in f]
for f in sorted(set(files)):
    text = open(f, encoding="utf-8").read()
    text = re.sub(r"```.*?```", "", text, flags=re.S)  # links inside code are examples, not links
    text = re.sub(r"`[^`\n]*`", "", text)
    for link in re.findall(r'(?:\]\(|href=")((?!https?:|mailto:|#)[^)#"\s]+)', text):
        n += 1
        if not os.path.exists(os.path.join(os.path.dirname(f), link)):
            bad.append(f"{f}: {link}")
print("\n".join(bad) or f"{n} relative links resolve")
raise SystemExit(1 if bad else 0)
PY
      { scripts/release.sh --check >>"$log" 2>&1; } || ok=0
      detail="$(tail -2 "$log" | tr '\n' ' ')" ;;
  esac
  emit "$c" "$([ "$ok" = 1 ] && echo passed || echo failed)" $((SECONDS - t0)) "$detail" "$log"
}

for c in $checks; do run_check "$c"; done
python3 -c 'import json,sys; print(json.dumps({"summary": {"passed": int(sys.argv[1]), "failed": int(sys.argv[2]), "skipped": int(sys.argv[3])}}))' $passed $failed $skipped
[ "$failed" = 0 ]
