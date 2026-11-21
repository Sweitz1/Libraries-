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

Android (needs JDK 17 and the Android SDK; CI does this for you on every push):

```bash
cd android && gradle testDebugUnitTest lintDebug assembleDebug
```

## Making a change

- Branch from `main`, keep the change focused, and add a test that fails without it.
- Update the README or `docs/` when behaviour changes, and add a line to `CHANGELOG.md` under *Unreleased*.
- Open a pull request; the template lists what reviewers check. CI must be green.

Starting points: [`templates/`](templates) has a plugin, an evaluation suite and a training config to copy. [`docs/REVIEW.md`](docs/REVIEW.md) maps every part of the code.

## Releasing (maintainers)

```bash
scripts/release.sh 0.16.0     # sets the version everywhere, runs the tests, builds, and prints the tag commands
```

Pushing the tag `v0.16.0` runs the release workflow, which builds the Python packages and the Android APK, and publishes them with checksums as a GitHub release.

Security problems: see [SECURITY.md](SECURITY.md).
