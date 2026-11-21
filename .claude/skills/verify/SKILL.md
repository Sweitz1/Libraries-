---
description: Runs Libra's checks for the parts a change touches (Python tests, the end-to-end smoke script, the Java client, the Android app's JVM tests) and reports what passed. Use before committing, after a fix, or when asked to check or test the code.
---

## What changed

!`git status --short`

!`git diff --stat HEAD 2>/dev/null | tail -1`

## Run the checks

Work from the repository root. Run every block whose files changed (look at the paths above), and always the Python block for code changes. Docs-only or tests-only changes need only the Python tests.

1. **Python** (anything under `libra/`, `tests/`, `.github/smoke.sh`, `pyproject.toml`). Install first if `libra` isn't on the PATH: `pip install -e .`
   ```bash
   python -m unittest discover -s tests        # about 40 s; expect "OK (skipped=1)" without OpenCV
   bash .github/smoke.sh > /tmp/libra-smoke.log 2>&1; echo "smoke exit $?"
   ```
   A traceback printed by `test_ecosystem` (a fake MCP server getting an empty body) is known noise, not a failure. Only the final `OK`/`FAILED` line counts.

2. **Java client** (`clients/java/`), needing JDK 11+ and Maven. Run it against a real server, as CI does:
   ```bash
   export LIBRA_HOME=$(mktemp -d)
   (libra serve --host 127.0.0.1 --port 8791 --token verify-token > "$LIBRA_HOME/serve.log" 2>&1 &)
   sleep 3
   (cd clients/java && LIBRA_TEST_URL=http://127.0.0.1:8791 LIBRA_TEST_TOKEN=verify-token mvn -q -B verify)
   pkill -f "port 8791"
   ```
   Results are in `clients/java/target/surefire-reports/`. A failing `LiveServerTest` can be a real Libra bug: check the server's behaviour before loosening the test.

3. **Android app** (`android/`). The SDK usually isn't available locally, so run the pure-Java parts with JUnit (download `junit-4.13.2.jar` and `hamcrest-core-1.3.jar` from Maven Central):
   ```bash
   javac -d /tmp/aout -cp junit-4.13.2.jar android/app/src/main/java/ai/libra/app/{PairLink,Updates}.java android/app/src/test/java/ai/libra/app/*.java
   java -cp /tmp/aout:junit-4.13.2.jar:hamcrest-core-1.3.jar org.junit.runner.JUnitCore ai.libra.app.PairLinkTest ai.libra.app.UpdatesTest
   ```
   Then check every XML file parses (XML forbids `--` inside comments, which once broke the manifest):
   ```bash
   python3 -c "import glob,xml.dom.minidom as m; [m.parse(f) for f in glob.glob('android/**/*.xml', recursive=True)]; print('xml ok')"
   ```
   The full Android build and lint run only in CI (`.github/workflows/android.yml`). After pushing, check that run.

4. **Workflows** (`.github/workflows/`): `python3 -c "import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob('.github/workflows/*.yml')]"`. For `release.yml`, also dry-run it manually on the branch (`workflow_dispatch`) before tagging.

5. **Docs** (`*.md`): relative links must exist. `scripts/release.sh --check` confirms the version agrees in `pyproject.toml`, `libra/__init__.py`, `android/app/build.gradle`, `clients/java/pom.xml` and `CHANGELOG.md`.

## Report

List each block as passed, failed (with the failing test and its message), or not run and why. Never call a change verified when a block that applies didn't run. Say what only CI or a real device can check (Android build and lint, the GPU, the Debian installer).
