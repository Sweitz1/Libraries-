## What this changes

## Why

## How it was tested

- [ ] `python -m unittest discover -s tests`
- [ ] `./.github/smoke.sh`
- [ ] Android only: `gradle testDebugUnitTest lintDebug` in `android/`

## Checklist

- [ ] No new dependencies (NumPy and the standard library only; the Android app uses only the framework)
- [ ] No pretrained models or outside AI services
- [ ] Skills that change something go through the trust rules
- [ ] Docs updated (README, `docs/`), and `CHANGELOG.md` has an entry
- [ ] No tokens, pairing links or personal data in code, tests or screenshots
