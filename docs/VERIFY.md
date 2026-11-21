# Verification: Libra 0.15.0 at commit `1db5559`

Everything on this page refers to **one commit**. Please review this exact version: earlier reviews
read older commits and reported problems that had already been fixed. (This page is committed just
after it, and regenerated with `scripts/verification_link.py --out docs/VERIFY.md`.)

- **Commit:** [`1db5559e982d9b782a928e2d57639b2ed67166dd`](https://github.com/Sweitz1/Libraries-/commit/1db5559e982d9b782a928e2d57639b2ed67166dd) (2026-11-21): Merge pull request #12: Stingward security audit (grade A) and CI job
- **Code at this commit:** https://github.com/Sweitz1/Libraries-/tree/1db5559e982d9b782a928e2d57639b2ed67166dd
- **Download:** https://github.com/Sweitz1/Libraries-/archive/1db5559e982d9b782a928e2d57639b2ed67166dd.zip
- **Size:** 158 Python test functions, 14 Claude Code project skills

## CI results for this commit

| Workflow | Result | Jobs |
|---|---|---|
| [tests](https://github.com/Sweitz1/Libraries-/actions/runs/44901286068) (push) | **success** | stingward: success, test (Python 3.10): success, test (Debian bullseye, Python 3.9): success, test (Python 3.13): success, java-client: success, test (Debian bookworm, Python 3.11): success |
| [android](https://github.com/Sweitz1/Libraries-/actions/runs/44901285876) (push) | **success** | apk: success |

The `stingward` job is a security scan (dependencies against OSV.dev, secrets, injection, SSRF, agent
and MCP settings, and every skill). Its log shows what it checked.

## Read these first

| File | What it is | Raw (for an AI) |
|---|---|---|
| [docs/REVIEW.md](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/docs/REVIEW.md) | map of every app, server and capability; responses to four earlier reviews (§9-12) | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/docs/REVIEW.md) |
| [SECURITY.md](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/SECURITY.md) | what counts as a vulnerability, and the automated checks | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/SECURITY.md) |
| [LEARN.md](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/LEARN.md) | a guided path through the code, lesson by lesson | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/LEARN.md) |
| [CHANGELOG.md](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/CHANGELOG.md) | what changed, by version | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/CHANGELOG.md) |
| [libra/server.py](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/server.py) | HTTP API: auth, limits, generation slots, jobs and pairing endpoints | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/server.py) |
| [libra/web.py](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/web.py) | web fetching: robots.txt, private-address guard, pinned connections (SSRF) | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/web.py) |
| [libra/trust.py](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/trust.py) | allow/ask/deny rules for skills; audit log redaction | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/trust.py) |
| [libra/skills.py](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/skills.py) | SkillRegistry.run, the single path every skill takes | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/skills.py) |
| [libra/jobs.py](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/jobs.py) | durable jobs: resume, approvals, outside-content gate | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/jobs.py) |
| [libra/checkpoint.py](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/checkpoint.py) | atomic, versioned model checkpoints | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/checkpoint.py) |
| [libra/devices.py](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/devices.py) | per-device, hashed, revocable tokens | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/libra/devices.py) |
| [android/app/src/main/java/ai/libra/app/MainActivity.java](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/android/app/src/main/java/ai/libra/app/MainActivity.java) | the Android app's WebView | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/android/app/src/main/java/ai/libra/app/MainActivity.java) |
| [.github/workflows/test.yml](https://github.com/Sweitz1/Libraries-/blob/1db5559e982d9b782a928e2d57639b2ed67166dd/.github/workflows/test.yml) | the CI that produced the results above | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/.github/workflows/test.yml) |

## Reproduce it

```bash
git clone https://github.com/Sweitz1/Libraries-.git && cd Libraries-
git checkout 1db5559e982d9b782a928e2d57639b2ed67166dd
pip install -e .
python -m unittest discover -s tests        # about 40 seconds
bash .github/smoke.sh                       # end-to-end command-line checks
bash .claude/skills/verify/scripts/verify.sh --all   # everything, one JSON line per check
```

## Not verified

Where to dig is on the [investigation page](INVESTIGATE.md): the attack surface, known risks and
hypotheses to test, for the same commit.

- A real deployment: Debian with systemd, Tailscale, Discord's servers, a GPU.
- The Android app on a real phone or emulator (it's built, unit-tested and linted in CI only).
- No professional security audit. The scanners above find common mistakes; they're not a penetration test.

## A prompt for an AI reviewer

```text
Review the Libra repository at commit 1db5559e982d9b782a928e2d57639b2ed67166dd only:
  https://github.com/Sweitz1/Libraries-/tree/1db5559e982d9b782a928e2d57639b2ed67166dd
Start with this file, which maps the code and answers four earlier reviews (sections 9-12):
  https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/docs/REVIEW.md
Don't re-report something those sections show as fixed unless you can show it still fails at this
commit. Then read this file and the files it names:
  https://raw.githubusercontent.com/Sweitz1/Libraries-/1db5559e982d9b782a928e2d57639b2ed67166dd/SECURITY.md
For every finding, give the file and line at this commit, what goes wrong with a concrete input,
and whether you verified it by reading the code or are inferring it. Say what you could not check.
```
