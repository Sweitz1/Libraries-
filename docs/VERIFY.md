# Verification: Libra 0.15.0 at commit `205f900`

Everything on this page refers to **one commit**. Please review this exact version: earlier reviews
read older commits and reported problems that had already been fixed. (This page is committed just
after it, and regenerated with `scripts/verification_link.py --out docs/VERIFY.md`.)

- **Commit:** [`205f900df691a3a68ad61d221260f54f6eb55242`](https://github.com/Sweitz1/Libraries-/commit/205f900df691a3a68ad61d221260f54f6eb55242) (2026-11-21): Merge pull request #18 from Sweitz1/ccr-6946e33f-4z60nj
- **Code at this commit:** https://github.com/Sweitz1/Libraries-/tree/205f900df691a3a68ad61d221260f54f6eb55242
- **Download:** https://github.com/Sweitz1/Libraries-/archive/205f900df691a3a68ad61d221260f54f6eb55242.zip
- **Size:** 178 Python test functions, 14 Claude Code project skills

## CI results for this commit

| Workflow | Result | Jobs |
|---|---|---|
| [android](https://github.com/Sweitz1/Libraries-/actions/runs/44906576636) (push) | **success** | apk: success |
| [tests](https://github.com/Sweitz1/Libraries-/actions/runs/44906576618) (push) | **success** | test (Python 3.13): success, stingward: success, test (Python 3.10): success, test (Debian bullseye, Python 3.9): success, java-client: success, test (Debian bookworm, Python 3.11): success |

The `stingward` job is a security scan (dependencies against OSV.dev, secrets, injection, SSRF, agent
and MCP settings, and every skill). Its log shows what it checked.

## Read these first

| File | What it is | Raw (for an AI) |
|---|---|---|
| [docs/REVIEW.md](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/docs/REVIEW.md) | map of every app, server and capability; responses to four earlier reviews (§9-12) | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/docs/REVIEW.md) |
| [SECURITY.md](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/SECURITY.md) | what counts as a vulnerability, and the automated checks | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/SECURITY.md) |
| [LEARN.md](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/LEARN.md) | a guided path through the code, lesson by lesson | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/LEARN.md) |
| [CHANGELOG.md](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/CHANGELOG.md) | what changed, by version | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/CHANGELOG.md) |
| [libra/server.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/server.py) | HTTP API: auth, limits, generation slots, jobs and pairing endpoints | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/libra/server.py) |
| [libra/web.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/web.py) | web fetching: robots.txt, private-address guard, pinned connections (SSRF) | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/libra/web.py) |
| [libra/trust.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/trust.py) | allow/ask/deny rules for skills; audit log redaction | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/libra/trust.py) |
| [libra/skills.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/skills.py) | SkillRegistry.run, the single path every skill takes | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/libra/skills.py) |
| [libra/jobs.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/jobs.py) | durable jobs: resume, approvals, outside-content gate | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/libra/jobs.py) |
| [libra/checkpoint.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/checkpoint.py) | atomic, versioned model checkpoints | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/libra/checkpoint.py) |
| [libra/devices.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/devices.py) | per-device, hashed, revocable tokens | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/libra/devices.py) |
| [android/app/src/main/java/ai/libra/app/MainActivity.java](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/android/app/src/main/java/ai/libra/app/MainActivity.java) | the Android app's WebView | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/android/app/src/main/java/ai/libra/app/MainActivity.java) |
| [.github/workflows/test.yml](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/.github/workflows/test.yml) | the CI that produced the results above | [raw](https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/.github/workflows/test.yml) |

## Reproduce it

```bash
git clone https://github.com/Sweitz1/Libraries-.git && cd Libraries-
git checkout 205f900df691a3a68ad61d221260f54f6eb55242
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
Review the Libra repository at commit 205f900df691a3a68ad61d221260f54f6eb55242 only:
  https://github.com/Sweitz1/Libraries-/tree/205f900df691a3a68ad61d221260f54f6eb55242
Start with this file, which maps the code and answers four earlier reviews (sections 9-12):
  https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/docs/REVIEW.md
Don't re-report something those sections show as fixed unless you can show it still fails at this
commit. Then read this file and the files it names:
  https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/SECURITY.md
For every finding, give the file and line at this commit, what goes wrong with a concrete input,
and whether you verified it by reading the code or are inferring it. Say what you could not check.
```
