---
name: verify
description: Runs Libra's checks for the parts a change touches (Python tests, the end-to-end smoke script, the Java client, the Android app's JVM tests) and reports what passed. Use before committing, after a fix, or when asked to check or test the code.
compatibility: Requires bash, git and Python 3.9+. The java check needs Maven and JDK 11+; android needs a JDK and network access to Maven Central once; workflows needs PyYAML. Missing tools make a check "skipped", never "passed".
---

## What changed

!`git status --short`

## Available scripts

- **`scripts/verify.sh`**: runs the checks, chosen from the files changed since `origin/main` plus uncommitted ones. Use `--only python,docs`, `--all`, or `--help` for the options. It prints one JSON line per check (`check`, `status`, `seconds`, `detail`, `log`) and a `summary` line on stdout, with progress on stderr.

  | Check | What it runs |
  |---|---|
  | `python` | the unit tests |
  | `smoke` | `.github/smoke.sh` |
  | `java` | `clients/java` with Maven, including the live test against a real `libra serve` it starts |
  | `android` | the app's pure-Java JUnit tests |
  | `xml` | every Android XML file parses (`--` inside a comment once broke the manifest) |
  | `workflows` | the workflows are valid YAML |
  | `docs` | relative links resolve, and `scripts/release.sh --check` passes |

  Exit codes: `0` all passed, `1` a check failed, `2` bad arguments, `3` not a Libra checkout.

## Workflow

1. Run it:
   ```bash
   bash scripts/verify.sh
   ```
   It takes about a minute for everything (`--all`); a docs-only change takes seconds.
2. For a `failed` check, read its `detail`, then the `log` file it names, before changing anything:
   - A traceback printed by `test_ecosystem` (a fake MCP server getting an empty body) is known noise. Only the `Ran …`/`OK`/`FAILED` lines count.
   - A failing `java` live test can be a real Libra bug: check the server's behaviour before loosening the test.
3. Fix, then run the failed checks again with `--only`.

## Report

List each check as passed, failed (with its detail) or skipped (with the reason). Never call a change verified when a check that applies was skipped. Say what only CI or a device can check:

- the full Android build and lint (`.github/workflows/android.yml`);
- `release.yml`: dry-run it on the branch before tagging;
- the GPU, and the Debian installer on a real server.
