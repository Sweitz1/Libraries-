#!/usr/bin/env python3
"""Write a verification page for one exact commit, for a person or an AI to review Libra.

Usage: scripts/verification_link.py [--commit SHA] [--out FILE] [--offline]

The page pins everything to one commit:
  - links to the code at that commit (a review of an older version reports fixed problems);
  - the CI runs for that commit and every job's result, fetched from the GitHub API;
  - raw file links an AI can read directly, a reviewing prompt, and what isn't verified.

Options:
  --commit SHA   the commit to describe (default: origin/main, else HEAD)
  --out FILE     write the page here (default: stdout)
  --offline      don't ask GitHub for CI results (the page says they weren't checked)
  -h, --help

Exit codes: 0 written, 1 a CI job for the commit failed (the page is still written), 2 bad arguments,
3 the commit isn't in this checkout, 4 CI hasn't finished for the commit (re-run later).

Examples:
  scripts/verification_link.py --out docs/VERIFY.md
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


def main(argv):
    if any(a in ("-h", "--help") for a in argv):
        print(__doc__.strip())
        return 0
    commit, out, offline, i = None, None, False, 0
    while i < len(argv):
        a = argv[i]
        if a in ("--commit", "--out"):
            if i + 1 >= len(argv):
                return fail(2, f"{a} needs a value.")
            if a == "--commit":
                commit = argv[i + 1]
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
        print(f"Error: {ref!r} isn't a commit in this checkout. Try git fetch origin main first.", file=sys.stderr)
        return 3
    sha = r.stdout.strip()
    date, subject = git("log", "-1", "--format=%cs%x00%s", sha).stdout.strip().split("\x00")
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
