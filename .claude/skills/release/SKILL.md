---
name: release
description: Prepares and checks a Libra release (version bump, changelog, dry run of the release workflow, tag, and checking the published files). Use only when the user asks to release, publish or tag a version.
disable-model-invocation: true
argument-hint: "X.Y.Z"
---

## Current state

!`sed -n 's/^version = "\(.*\)"/version in pyproject.toml: \1/p' pyproject.toml`

!`git tag --list 'v*' | tail -3`

!`sed -n '/^## \[Unreleased\]/,/^## \[/p' CHANGELOG.md | head -20`

## Steps

A release publishes outside the repository: a GitHub release, and a package on GitHub Packages. Confirm the version with the user before tagging.

1. **Changelog.** Everything user-visible since the last release goes under `## [Unreleased]` in `CHANGELOG.md` (Added / Security / Fixed). Say what isn't verified.
2. **Bump.** Run `scripts/release.sh X.Y.Z` on a clean tree. It sets the version in `pyproject.toml`, `libra/__init__.py`, `android/app/build.gradle` (and raises `versionCode`), `clients/java/pom.xml` and the changelog. It then runs the tests and builds the Python packages, and prints the commands to publish. It never pushes.
3. **Dry run.** Push the branch, open a PR, then run `.github/workflows/release.yml` by hand (`workflow_dispatch`) on the branch. A manual run builds everything (Python, APK, Java jar, checksums, notes) and publishes nothing. All of `python`, `android`, `java` and `publish` must pass. Merge only then.
4. **Tag.** The release starts when a `vX.Y.Z` tag lands on `main`. A cloud session can usually only push its own branch, so tag pushes get a 403. If so, give the user the link `https://github.com/Sweitz1/Libraries-/releases/new` (tag `vX.Y.Z`, target `main`, Publish), or the `git tag -a … && git push origin vX.Y.Z` commands.
5. **Check what was published.** The release run's jobs, then the release page:
   - files: `libra_lm-X.Y.Z-py3-none-any.whl`, `libra_lm-X.Y.Z.tar.gz`, `libra.apk`, `libra-client-X.Y.Z.jar` (plus its sources and javadoc jars), `SHA256SUMS`
   - notes: the changelog section
   - `ai.libra:libra-client` under the repository's Packages

## Things that bite

- **Android signing.** Without the secrets `LIBRA_KEYSTORE_BASE64`, `LIBRA_KEYSTORE_PASSWORD`, `LIBRA_KEY_ALIAS` and `LIBRA_KEY_PASSWORD`, each build signs with a new temporary debug key, and the APKs can't update one another. The run shows a warning when that happens. To re-sign after adding the secrets, re-run the tag's workflow: the publish step replaces the files of an existing release.
- **The version must match the tag**, or the `python` job stops (`scripts/release.sh --check`).
- **GitHub Packages:** the registry URL in `pom.xml` uses the lowercase owner (`sweitz1`). Deploying uses the workflow's `GITHUB_TOKEN` with `packages: write`. Installers still need a classic token with `read:packages`, even for a public package.
- **Never** commit keystores, tokens or pairing links.
