# Contributing to Libra

Thanks for helping. A few ground rules keep Libra what it is:

1. **Original code only.** No pretrained weights, no outside AI services, no copied model code.
2. **No new dependencies.** Python code uses NumPy and the standard library. The Android app uses only the Android framework.
3. **Changes go through trust.** Any skill that changes something must be marked `write` or `danger`, so the trust rules apply.
4. **Say what isn't verified.** If something wasn't run for real (a GPU, a phone, a live service), write that in the docs.

## Set up

```bash
git clone https://github.com/Sweitz1/Libraries-.git && cd Libraries-
pip install -e .
python -m unittest discover -s tests     # about 40 seconds
./.github/smoke.sh                       # end-to-end command-line checks
```

Java client (needs JDK 11+ and Maven):

```bash
cd clients/java && mvn verify
```

Android (needs JDK 17 and the Android SDK; CI does this for you on every push):

```bash
cd android && gradle testDebugUnitTest lintDebug assembleDebug
```

## Making a change

- Branch from `main`, keep the change focused, and add a test that fails without it.
- Update the README or `docs/` when behaviour changes, and add a line to `CHANGELOG.md` under *Unreleased*.
- Open a pull request; the template lists what reviewers check. CI must be green.

**Working with Claude Code?** The repository has project skills in [`.claude/skills/`](.claude/skills):

| Skill | For |
|---|---|
| `/verify` | Runs the checks for the parts you changed. Claude also runs it before each commit |
| `/run-libra` | Starts a server and drives the app in a headless browser |
| `/add-libra-skill` | Adds a Libra skill the right way: risk, outside content, tests, planner suite |
| `/answer-review` | Works through a code review point by point and records the response in `docs/REVIEW.md` |
| `/release X.Y.Z` | Cuts a release. You start it yourself: Claude never starts it on its own |

Starting points: [`templates/`](templates) has a plugin, an evaluation suite and a training config to copy. [`docs/REVIEW.md`](docs/REVIEW.md) maps every part of the code.

## Releasing (maintainers)

```bash
scripts/release.sh 0.16.0     # sets the version everywhere, runs the tests, builds, and prints the tag commands
```

Pushing the tag `v0.16.0` runs the release workflow, which builds the Python packages, the Android APK and the Java client, publishes them with checksums as a GitHub release, and deploys the Java client to GitHub Packages.

Security problems: see [SECURITY.md](SECURITY.md).
