#!/usr/bin/env bash
# Usage: scripts/validate.sh [--kind KIND] FILE [FILE ...]
#
# Validates Libra configuration files before they're used, with Libra's own loaders.
#
# Kinds (detected from the name and contents, or forced with --kind):
#   auto      a `libra auto` training config (.json/.yaml with "sources"): loads it, checks each
#             source and setting, and flags unknown keys (a typo is otherwise silently ignored)
#   suite     a benchmark suite (.jsonl) for `libra eval model` or `libra eval plan`
#   trust     trust rules (trust.json): every decision is allow, ask or deny
#   goals     saved agent goals (goals.json): schedules, permissions
#   plugin    a Libra plugin (.py): parsed, never run; every @skill needs a valid risk
#
# Options:
#   --kind KIND   treat every FILE as this kind
#   -h, --help
#
# Output: one JSON object per file on stdout:
#   {"file", "kind", "valid": true|false, "problems": [...], "warnings": [...]}
# then {"summary": {"valid": n, "invalid": n}}. Problems make a file invalid; warnings don't.
#
# Exit codes: 0 all valid, 1 a file is invalid, 2 invalid arguments, 3 a file doesn't exist.
#
# Examples:
#   scripts/validate.sh ~/libra-auto.json
#   scripts/validate.sh --kind suite my-suite.jsonl
#   scripts/validate.sh plugins/*.py ~/.libra/trust.json
set -uo pipefail

kind=""
files=()
while [ $# -gt 0 ]; do
  case "$1" in
    --kind) kind="${2:-}"; shift 2 || true
      case "$kind" in auto|suite|trust|goals|plugin) ;;
        *) printf "Error: --kind must be one of: auto, suite, trust, goals, plugin. Received: '%s'\nUsage: scripts/validate.sh [--kind KIND] FILE ...\n" "$kind" >&2; exit 2 ;; esac ;;
    -h|--help) sed -n '2,/^set -uo/p' "$0" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
    -*) printf "Error: unknown option '%s'.\nUsage: scripts/validate.sh [--kind KIND] FILE ...\n" "$1" >&2; exit 2 ;;
    *) files+=("$1"); shift ;;
  esac
done
[ ${#files[@]} -gt 0 ] || { printf 'Error: give at least one FILE to validate.\nUsage: scripts/validate.sh [--kind KIND] FILE ...   (--help for the kinds)\n' >&2; exit 2; }
for f in "${files[@]}"; do
  [ -f "$f" ] || { printf "Error: no such file: '%s'\n" "$f" >&2; exit 3; }
done

root="$(cd "$(dirname "$0")/../../../.." && pwd)"   # this file is .claude/skills/libra-data/scripts/validate.sh
PYTHONPATH="$root${PYTHONPATH:+:$PYTHONPATH}" exec python3 - "$kind" "${files[@]}" <<'PY'
import ast, json, os, re, sys

forced, files = sys.argv[1], sys.argv[2:]


def detect(path, text):
    name = os.path.basename(path).lower()
    if name.endswith(".py"):
        return "plugin"
    if name.endswith(".jsonl"):
        return "suite"
    if name == "trust.json":
        return "trust"
    if name == "goals.json":
        return "goals"
    if name.endswith((".json", ".yaml", ".yml")) and re.search(r'"?sources"?\s*:', text):
        return "auto"
    return ""


def check_auto(path, text, problems, warnings):
    from libra import auto

    try:
        raw = json.loads(text) if not path.endswith((".yaml", ".yml")) else __import__("libra.yamlish", fromlist=["loads"]).loads(text)
    except ValueError as exc:
        problems.append(f"not valid {'YAML' if path.endswith(('.yaml', '.yml')) else 'JSON'}: {exc}")
        return
    if not isinstance(raw, dict):
        problems.append("the config must be an object")
        return
    for key in raw:
        if key not in auto.TEMPLATE:
            warnings.append(f"unknown setting {key!r} (ignored; known: {', '.join(sorted(auto.TEMPLATE))})")
        elif isinstance(auto.TEMPLATE[key], dict) and isinstance(raw[key], dict):
            for sub in raw[key]:
                if sub not in auto.TEMPLATE[key]:
                    warnings.append(f"unknown setting {key}.{sub!r} (ignored; known: {', '.join(sorted(auto.TEMPLATE[key]))})")
    try:
        cfg = auto.load_config(path)
    except Exception as exc:
        problems.append(str(exc))
        return
    kinds = ("crawl", "feed", "sitemap", "url", "gutenberg", "files")
    for i, src in enumerate(cfg["sources"], 1):
        if not isinstance(src, dict) or not any(k in src for k in kinds):
            problems.append(f"source {i} needs one of: {', '.join(kinds)}")
            continue
        for k in ("crawl", "feed", "sitemap", "url"):
            if k in src and not str(src[k]).startswith(("http://", "https://")):
                problems.append(f"source {i}: {k} must be an http(s) address, got {src[k]!r}")
        if "files" in src and not os.path.exists(os.path.expanduser(str(src["files"]))):
            warnings.append(f"source {i}: {src['files']!r} doesn't exist (yet)")
        for rx in ("include", "exclude"):
            if src.get(rx):
                try:
                    re.compile(src[rx])
                except re.error as exc:
                    problems.append(f"source {i}: {rx} isn't a valid regular expression: {exc}")
        if "example.com" in str(src.get("crawl", src.get("feed", src.get("sitemap", src.get("url", ""))))):
            warnings.append(f"source {i} still points at example.com (the template's placeholder)")
    g = cfg["gate"]
    if not 0 < float(g["holdout_fraction"]) < 0.5:
        problems.append("gate.holdout_fraction must be between 0 and 0.5")
    try:
        auto._eval_suite(cfg)
    except ValueError as exc:
        problems.append(str(exc))
    mc = cfg["model_config"]
    if int(mc["embd"]) % int(mc["heads"]):
        problems.append(f"model_config.embd ({mc['embd']}) must be divisible by heads ({mc['heads']})")
    if not cfg.get("enabled", True):
        warnings.append("enabled is false: scheduled runs will skip it")


def check_suite(path, text, problems, warnings):
    from libra.evaluate import load_suite

    try:
        items = load_suite(path)
    except ValueError as exc:
        problems.append(str(exc))
        return
    if not items:
        problems.append("the suite has no items")
    kinds = set()
    for n, it in enumerate(items, 1):
        if not isinstance(it, dict):
            problems.append(f"item {n}: must be a JSON object")
        elif "goal" in it:
            kinds.add("plan")
            if not it.get("refuse") and not (isinstance(it.get("skills"), list) and it["skills"]):
                problems.append(f"item {n}: a planner item needs \"skills\": [...] or \"refuse\": true")
        elif "prompt" in it:
            kinds.add("model")
            if "choices" in it:
                ch = it["choices"]
                if not isinstance(ch, list) or len(ch) < 2:
                    problems.append(f"item {n}: \"choices\" needs at least 2 options")
                elif not isinstance(it.get("answer", 0), int) or not 0 <= it.get("answer", 0) < len(ch):
                    problems.append(f"item {n}: \"answer\" must be an index into choices (0-{len(ch) - 1})")
            elif not (isinstance(it.get("expect"), list) and it["expect"]):
                problems.append(f"item {n}: a model item needs \"choices\" (and \"answer\") or \"expect\": [...]")
        else:
            problems.append(f"item {n}: needs \"goal\" (planner suite) or \"prompt\" (model suite)")
    if len(kinds) > 1:
        problems.append("mixes planner items (goal) and model items (prompt); keep them in separate files")


def check_trust(path, text, problems, warnings):
    from libra.trust import DECISIONS, RISKS

    try:
        data = json.loads(text)
    except ValueError as exc:
        problems.append(f"not valid JSON: {exc}")
        return
    for target, d in (data.get("rules") or {}).items():
        if d not in DECISIONS:
            problems.append(f"rule {target!r}: {d!r} isn't one of {', '.join(DECISIONS)}")
    for risk, d in (data.get("defaults") or {}).items():
        if risk not in RISKS:
            problems.append(f"default for {risk!r}: the risks are {', '.join(RISKS)}")
        elif d not in DECISIONS:
            problems.append(f"default for {risk}: {d!r} isn't one of {', '.join(DECISIONS)}")
    if (data.get("defaults") or {}).get("danger") == "allow":
        warnings.append("danger skills are allowed without asking")


def check_goals(path, text, problems, warnings):
    from libra.agent import check_schedule

    try:
        goals = json.loads(text)
    except ValueError as exc:
        problems.append(f"not valid JSON: {exc}")
        return
    for name, g in goals.items():
        if not re.fullmatch(r"[A-Za-z0-9][\w-]{0,40}", name):
            problems.append(f"goal name {name!r}: letters, digits, - and _ (up to 40)")
        if not isinstance(g, dict) or not str(g.get("goal") or "").strip():
            problems.append(f"goal {name!r}: has no goal text")
            continue
        try:
            check_schedule(g.get("every"))
        except ValueError as exc:
            problems.append(f"goal {name!r}: {exc}")
        perms = g.get("permissions")
        if perms is None:
            warnings.append(f"goal {name!r}: saved before per-goal permissions, so it may use any skill (save it again to limit it)")
        elif perms.get("preapproved") and perms.get("skills") is not None and not set(perms["preapproved"]) <= set(perms["skills"]):
            problems.append(f"goal {name!r}: pre-approves skills it isn't allowed to use")


def check_plugin(path, text, problems, warnings):
    from libra.trust import RISKS

    try:
        tree = ast.parse(text, filename=path)
    except SyntaxError as exc:
        problems.append(f"syntax error on line {exc.lineno}: {exc.msg}")
        return
    found = 0
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            call = dec if isinstance(dec, ast.Call) else None
            fn = call.func if call else dec
            if not (getattr(fn, "id", None) == "skill" or getattr(fn, "attr", None) == "skill"):
                continue
            found += 1
            kw = {k.arg: k.value for k in (call.keywords if call else [])}
            if "risk" not in kw:
                warnings.append(f"skill {node.name!r}: no risk given, so it counts as \"write\" (asks first)")
            elif not (isinstance(kw["risk"], ast.Constant) and kw["risk"].value in RISKS):
                problems.append(f"skill {node.name!r}: risk must be one of {', '.join(RISKS)}")
            if "external" not in kw and re.search(r"\b(urlopen|requests\.|Fetcher|fetch)\b", ast.get_source_segment(text, node) or ""):
                warnings.append(f"skill {node.name!r}: it seems to read from outside; mark it external=True so its output can't drive changes unasked")
    if not found:
        warnings.append("no @skill functions found")
    if re.search(r"\b(subprocess|os\.system|eval\(|exec\()", text):
        warnings.append("runs commands or evaluates code: review it before loading (plugins run with your permissions)")


checks = {"auto": check_auto, "suite": check_suite, "trust": check_trust, "goals": check_goals, "plugin": check_plugin}
valid = invalid = 0
for path in files:
    with open(path, encoding="utf-8", errors="replace") as f:
        text = f.read()
    kind = forced or detect(path, text)
    problems, warnings = [], []
    if not kind:
        problems.append("can't tell what kind of file this is; pass --kind auto|suite|trust|goals|plugin")
    else:
        try:
            checks[kind](path, text, problems, warnings)
        except Exception as exc:  # a check that crashes is reported, not hidden
            problems.append(f"couldn't check it: {type(exc).__name__}: {exc}")
    ok = not problems
    valid += ok
    invalid += not ok
    print(json.dumps({"file": path, "kind": kind or None, "valid": ok, "problems": problems, "warnings": warnings}))
print(json.dumps({"summary": {"valid": valid, "invalid": invalid}}))
sys.exit(0 if not invalid else 1)
PY
