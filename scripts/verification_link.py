#!/usr/bin/env python3
"""Write a verification or an investigation page for one exact commit, for a person or an AI reviewing Libra.

Usage: scripts/verification_link.py [--kind verify|investigate] [--commit SHA] [--out FILE] [--offline]

Both pages pin everything to one commit (a review of an older version reports fixed problems).
  verify       what is proven: the CI runs for that commit and every job's result (from the GitHub API),
               key files, raw links an AI can read, how to reproduce it, and what isn't verified
  investigate  where to dig: the attack surface and trust boundaries, known residual risks,
               concrete hypotheses to test with where to start, and how to report what you find

Options:
  --kind KIND    verify (default) or investigate
  --commit SHA   the commit to describe (default: origin/main, else HEAD)
  --out FILE     write the page here (default: stdout)
  --offline      don't ask GitHub for CI results (the page says they weren't checked)
  -h, --help

Exit codes: 0 written, 1 a CI job for the commit failed (the page is still written), 2 bad arguments,
3 the commit isn't in this checkout, 4 CI hasn't finished for the commit (re-run later).

Examples:
  scripts/verification_link.py --out docs/VERIFY.md
  scripts/verification_link.py --kind investigate --out docs/INVESTIGATE.md
  scripts/verification_link.py --commit 1db5559 --offline
"""
import json
import os
import re
import subprocess
import sys
import urllib.request

REPO = "Sweitz1/Libraries-"
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
USAGE = "Usage: scripts/verification_link.py [--commit SHA] [--out FILE] [--offline]   (--help for details)"
KEY_FILES = [
    ("docs/REVIEW.md", "map of every app, server and capability; responses to four earlier reviews (§9-12)"),
    ("SECURITY.md", "what counts as a vulnerability, and the automated checks"),
    ("LEARN.md", "a guided path through the code, lesson by lesson"),
    ("CHANGELOG.md", "what changed, by version"),
    ("libra/server.py", "HTTP API: auth, limits, generation slots, jobs and pairing endpoints"),
    ("libra/web.py", "web fetching: robots.txt, private-address guard, pinned connections (SSRF)"),
    ("libra/trust.py", "allow/ask/deny rules for skills; audit log redaction"),
    ("libra/skills.py", "SkillRegistry.run, the single path every skill takes"),
    ("libra/jobs.py", "durable jobs: resume, approvals, outside-content gate"),
    ("libra/checkpoint.py", "atomic, versioned model checkpoints"),
    ("libra/devices.py", "per-device, hashed, revocable tokens"),
    ("android/app/src/main/java/ai/libra/app/MainActivity.java", "the Android app's WebView"),
    (".github/workflows/test.yml", "the CI that produced the results above"),
]


def git(*args):
    return subprocess.run(["git", "-C", ROOT, *args], capture_output=True, text=True)


def fail(code, message):
    print(f"Error: {message}\n{USAGE}", file=sys.stderr)
    return code


def get_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/vnd.github+json",
                                               "User-Agent": "libra-verification-link"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def ci_results(sha):
    """Every workflow run for the commit, with its jobs. Raises on network errors."""
    runs = get_json(f"https://api.github.com/repos/{REPO}/actions/runs?head_sha={sha}&per_page=50")["workflow_runs"]
    out, seen = [], set()
    for run in runs:  # newest first; the same commit can be tested on a branch and again on main
        if run.get("path", "").startswith("dynamic/") or run["name"] in seen:  # dynamic/: GitHub's own runs
            continue
        seen.add(run["name"])
        jobs = get_json(run["jobs_url"] + "?per_page=100")["jobs"]
        out.append({"name": run["name"], "event": run["event"], "status": run["status"],
                    "conclusion": run["conclusion"], "url": run["html_url"],
                    "jobs": [(j["name"], j["status"], j["conclusion"]) for j in jobs]})
    return out


def counts(sha):
    files = git("ls-tree", "-r", "--name-only", sha).stdout.split()
    tests = 0
    for f in files:
        if re.fullmatch(r"tests/test_\w+\.py", f):
            tests += len(re.findall(r"^\s+def test_", git("show", f"{sha}:{f}").stdout, re.M))
    skills = sorted({f.split("/")[2] for f in files if re.fullmatch(r"\.claude/skills/[^/]+/SKILL\.md", f)})
    version = re.search(r'^version = "([^"]+)"', git("show", f"{sha}:pyproject.toml").stdout, re.M)
    return {"python_tests": tests, "skills": skills, "version": version.group(1) if version else "?"}


def page(sha, info, ci, date, subject):
    short = sha[:7]
    blob = f"https://github.com/{REPO}/blob/{sha}"
    raw = f"https://raw.githubusercontent.com/{REPO}/{sha}"
    L = [f"# Verification: Libra {info['version']} at commit `{short}`", "",
         "Everything on this page refers to **one commit**. Please review this exact version: earlier reviews",
         "read older commits and reported problems that had already been fixed. (This page is committed just",
         "after it, and regenerated with `scripts/verification_link.py --out docs/VERIFY.md`.)", "",
         f"- **Commit:** [`{sha}`](https://github.com/{REPO}/commit/{sha}) ({date}): {subject}",
         f"- **Code at this commit:** https://github.com/{REPO}/tree/{sha}",
         f"- **Download:** https://github.com/{REPO}/archive/{sha}.zip",
         f"- **Size:** {info['python_tests']} Python test functions, {len(info['skills'])} Claude Code project skills", ""]
    L += ["## CI results for this commit", ""]
    if ci is None:
        L += ["Not checked when this page was written (`--offline`). See "
              f"https://github.com/{REPO}/actions?query=branch%3Amain", ""]
    elif not ci:
        L += ["No CI runs found for this commit yet.", ""]
    else:
        L += ["| Workflow | Result | Jobs |", "|---|---|---|"]
        for run in ci:
            result = run["conclusion"] or run["status"]
            jobs = ", ".join(f"{n}: {c or s}" for n, s, c in run["jobs"])
            L.append(f"| [{run['name']}]({run['url']}) ({run['event']}) | **{result}** | {jobs} |")
        L += ["", "The `stingward` job is a security scan (dependencies against OSV.dev, secrets, injection, SSRF, agent",
              "and MCP settings, and every skill). Its log shows what it checked.", ""]
    L += ["## Read these first", "", "| File | What it is | Raw (for an AI) |", "|---|---|---|"]
    for path, what in KEY_FILES:
        L.append(f"| [{path}]({blob}/{path}) | {what} | [raw]({raw}/{path}) |")
    L += ["", "## Reproduce it", "", "```bash", f"git clone https://github.com/{REPO}.git && cd Libraries-",
          f"git checkout {sha}", "pip install -e .",
          "python -m unittest discover -s tests        # about 40 seconds",
          "bash .github/smoke.sh                       # end-to-end command-line checks",
          "bash .claude/skills/verify/scripts/verify.sh --all   # everything, one JSON line per check", "```", "",
          "## Not verified", "",
          "Where to dig is on the [investigation page](INVESTIGATE.md): the attack surface, known risks and",
          "hypotheses to test, for the same commit.", "",
          "- A real deployment: Debian with systemd, Tailscale, Discord's servers, a GPU.",
          "- The Android app on a real phone or emulator (it's built, unit-tested and linted in CI only).",
          "- No professional security audit. The scanners above find common mistakes; they're not a penetration test.", "",
          "## A prompt for an AI reviewer", "", "```text",
          f"Review the Libra repository at commit {sha} only:",
          f"  https://github.com/{REPO}/tree/{sha}",
          "Start with this file, which maps the code and answers four earlier reviews (sections 9-12):",
          f"  {raw}/docs/REVIEW.md",
          "Don't re-report something those sections show as fixed unless you can show it still fails at this",
          "commit. Then read this file and the files it names:",
          f"  {raw}/SECURITY.md",
          "For every finding, give the file and line at this commit, what goes wrong with a concrete input,",
          "and whether you verified it by reading the code or are inferring it. Say what you could not check.",
          "```", ""]
    return "\n".join(L)


def investigation_page(sha, info, date, subject):
    blob = f"https://github.com/{REPO}/blob/{sha}"
    raw = f"https://raw.githubusercontent.com/{REPO}/{sha}"
    f = lambda path, label=None: f"[{label or path}]({blob}/{path})"  # noqa: E731
    L = [f"# Investigation: Libra {info['version']} at commit `{sha[:7]}`", "",
         "Where to look for problems. The [verification page](VERIFY.md) says what is proven; this page says",
         "what isn't, and where a careful reviewer should push. Everything refers to **one commit**:",
         f"[`{sha}`](https://github.com/{REPO}/commit/{sha}) ({date}): {subject}.", "",
         "Report what you find privately: https://github.com/" + REPO + "/security/advisories/new", "",
         "## Attack surface", "",
         "| Entry point | What an attacker controls | Defences | Start reading |", "|---|---|---|---|",
         "| HTTP API (`libra serve`) | requests from the network | bearer token on every path except `/health` and the app's static files; lockout; body, connection and rate limits; generation slots | " + f("libra/server.py") + " `_authorized`, `OPEN_PATHS`, `_validate` |",
         "| Device tokens and pairing | a stolen or leaked pairing link | per-device tokens, stored only as SHA-256; revocation cascades; links never carry the main token | " + f("libra/devices.py") + ", `server.py` `_pair` and `_revoke` |",
         "| Web fetching (skills, crawler, training) | page contents, redirects, DNS answers | robots.txt; non-public addresses refused; connects to the address it checked | " + f("libra/web.py") + " `_blocked_ip`, `_safe_connect` |",
         "| Content from the web, APIs, memory | text that looks like instructions | marked as outside content; a change that uses it waits for a person | " + f("libra/jobs.py") + ", `agent.py` `_outside_content_gate` |",
         "| Skills that change things | the goal or chat message | trust rules (any deny wins; ask means no when nobody can be asked); audit log with redaction | " + f("libra/trust.py") + ", `skills.py` `SkillRegistry.run` |",
         "| MCP servers, OpenAPI specs, plugins | tool descriptions and code you install | plugins and MCP servers run with your permissions **by design**; descriptions are data | " + f("libra/mcp.py") + ", " + f("libra/openapi.py") + " |",
         "| Unattended training | crawled text | filters, masking of e-mails and phones, a held-out and behaviour gate before a model is kept | " + f("libra/auto.py") + " |",
         "| Debian installer (runs as root) | its arguments | fixed paths, a shell-less service user, systemd hardening | " + f("deploy/install-debian.sh") + " |",
         "| Android app | the server's pages | a WebView without a JavaScript bridge; other origins open in the browser; scanned links only fill the form | " + f("android/app/src/main/java/ai/libra/app/MainActivity.java", "MainActivity.java") + " |",
         "| Discord bot | messages in the channels it's in | answers only messages addressed to it; an allow list for goals and memory; no mentions in replies | " + f("libra/discord.py") + " |",
         "", "## Known residual risks", "",
         "Already written down, so a finding here should show a *concrete* exploit or a cheap fix:", "",
         "1. **Proxies:** ignored unless `LIBRA_USE_PROXY=1` marks one as trusted; a trusted proxy resolves names again itself, so split-horizon DNS is its responsibility.",
         "2. **MCP over HTTP and Discord's REST API** use their configured URLs directly (trusted configuration, often `localhost`).",
         "3. **The web app's sign-in** is an HttpOnly session cookie: script in the app's origin can't read it, but can use it while the page is open (CSP `script-src 'self'` limits it).",
         "4. **Rate limits and connection caps are per address**: many clients behind one NAT share a budget.",
         "5. **Two trainers writing one checkpoint folder** at once would race; `libra auto` holds a lock file against it.",
         "6. **Planner and result checks are rule-based**, read from the goal's wording, and can be fooled by phrasing.",
         f"\nSource: {f('docs/REVIEW.md')}, sections 9 to 13.", "",
         "## Hypotheses worth testing", "",
         "Each is a claim the code makes. Try to break it; the starting point is where the defence lives.", "",
         "| # | Claim | Try | Start at |", "|---|---|---|---|",
         "| H1 | No API path answers without a token, except `/health` and static files | odd paths: trailing `/`, `//`, `%2F`, `?` tricks, `HEAD` and other methods | `server.py` `do_GET`, `do_POST`, `_authorized` |",
         "| H2 | A device token can't act beyond its own device | revoke another device; pair new devices from a revoked one; replay after revocation | `server.py` `_revoke`, " + f("libra/devices.py") + " `verify` |",
         "| H3 | Web fetching never reaches a non-public address | IPv6 forms, `[::ffff:127.0.0.1]`, decimal or octal IPv4, DNS that changes between lookups, redirect chains, `file:`/`gopher:` | `web.py`, " + f("tests/test_ssrf.py") + " |",
         "| H4 | Outside content can't trigger a change without a person | Discord messages, chat with `libra_auto_skills`, memory recall feeding a write step, `foreach` items from a page | `jobs.py` `run`, `agent.py`, " + f("tests/test_jobs.py") + " `PermissionsAndOutsideContentTest` |",
         "| H5 | Any applicable deny wins | prefix rules, catalog skill names with dots, upper/lower case | `trust.py` `Policy.decide` |",
         "| H6 | Secrets never reach the audit log | nested arguments, URLs with credentials, token-shaped strings in lists | `trust.py` `redact` |",
         "| H7 | No path reaches outside its folder | job ids, `libra kb add`, translation files, the static file table | `jobs.py` `_dir`, " + f("libra/knowledge.py") + " |",
         "| H8 | A checkpoint can't run code when loaded | a crafted `model.npz` (pickled objects), a huge `config.json` | " + f("libra/checkpoint.py") + " `load` (NumPy's `allow_pickle` default is off) |",
         "| H9 | One client can't exhaust the server | slow bodies, many connections, long generations, huge messages, pathological regex input to the planner | `server.py` `LibraHTTPServer`, `Engine.acquire`, " + f("libra/agent.py") + " |",
         "| H10 | An interrupted job never repeats a change by itself | `kill -9` during a write step, then resume, then the agent timer | `jobs.py` `_recover`, `tests/test_jobs.py` |",
         "", "## Not verified at all", "",
         "These were never run for real, so anything here is open ground:",
         "a Debian deployment under systemd, Tailscale, Discord's real servers, a GPU, the Android app on a device,",
         "real MCP servers and public APIs, and live websites. There has been no professional security audit.", "",
         "## How to report", "",
         "- **Security problems:** privately, at https://github.com/" + REPO + "/security/advisories/new (never as a public issue).",
         "- **Other problems:** an issue, using the bug form.",
         "- Include: the commit (`" + sha[:7] + "`), the file and line, a concrete input that shows it, and whether you ran it",
         "  or are reading the code. A failing test is the best report.", "",
         "## A prompt for an AI investigator", "", "```text",
         f"Investigate the Libra repository at commit {sha} only:",
         f"  https://github.com/{REPO}/tree/{sha}",
         "Read the attack surface, residual risks and hypotheses on this page first:",
         f"  https://raw.githubusercontent.com/{REPO}/main/docs/INVESTIGATE.md",  # committed after the commit it describes
         "and the responses to earlier reviews (sections 9-12) here:",
         f"  {raw}/docs/REVIEW.md",
         "Try to break each hypothesis H1-H10. For each, say: broken (with the file, line and a concrete input),",
         "holds (what you checked), or not checked. Don't report a listed residual risk unless you show an exploit",
         "or a fix. Say clearly which results come from reading the code and which you ran.",
         "```", ""]
    return "\n".join(L)


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__.strip())
        return 0
    commit, out, offline, kind, i = None, None, False, "verify", 0
    while i < len(argv):
        a = argv[i]
        if a in ("--commit", "--out", "--kind"):
            if i + 1 >= len(argv):
                return fail(2, f"{a} needs a value.")
            if a == "--commit":
                commit = argv[i + 1]
            elif a == "--kind":
                kind = argv[i + 1]
                if kind not in ("verify", "investigate"):
                    return fail(2, f"--kind must be verify or investigate. Received: {kind!r}")
            else:
                out = argv[i + 1]
            i += 2
        elif a == "--offline":
            offline, i = True, i + 1
        else:
            return fail(2, f"unknown argument {a!r}.")
    ref = commit or ("origin/main" if git("rev-parse", "--verify", "-q", "origin/main").returncode == 0 else "HEAD")
    r = git("rev-parse", "--verify", "-q", ref + "^{commit}")
    if r.returncode != 0:
        hint = (r.stderr.strip().splitlines() or [""])[-1]
        if "dubious ownership" in hint or "safe.directory" in hint:
            print(f"Error: git won't read this checkout: {hint}\n"
                  f"       Run: git config --global --add safe.directory {ROOT}", file=sys.stderr)
        else:
            print(f"Error: {ref!r} isn't a commit in this checkout. Try git fetch origin main first.", file=sys.stderr)
        return 3
    sha = r.stdout.strip()
    date, subject = git("log", "-1", "--format=%cs%x00%s", sha).stdout.strip().split("\x00")
    if kind == "investigate":  # no CI lookup needed: the page is about where to look, not what passed
        text = investigation_page(sha, counts(sha), date, subject)
        if out:
            with open(out, "w", encoding="utf-8") as f:
                f.write(text)
            print(f"wrote {out} for {sha[:7]}", file=sys.stderr)
            print(f"share: https://github.com/{REPO}/blob/main/{os.path.relpath(os.path.abspath(out), ROOT)}", file=sys.stderr)
        else:
            print(text)
        return 0
    ci = None
    if not offline:
        try:
            ci = ci_results(sha)
        except Exception as exc:  # no network: say so on the page, don't invent results
            print(f"warning: couldn't read CI results ({exc}); writing the page without them", file=sys.stderr)
    text = page(sha, counts(sha), ci, date, subject)
    if out:
        with open(out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"wrote {out} for {sha[:7]}", file=sys.stderr)
    else:
        print(text)
    if out:  # the page is committed after the commit it describes, so share it from main
        print(f"share: https://github.com/{REPO}/blob/main/{os.path.relpath(os.path.abspath(out), ROOT)}", file=sys.stderr)
    if ci and any(run["status"] != "completed" for run in ci):
        print("note: CI is still running for this commit; run it again when it finishes", file=sys.stderr)
        return 4
    if ci and any(run["conclusion"] not in ("success", "skipped") for run in ci):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
