#!/usr/bin/env bash
# Prepare a release: set the version everywhere, run the tests, build the Python packages,
# and print the commands that publish it. It never pushes anything itself.
#
#   scripts/release.sh 0.16.0        prepare version 0.16.0
#   scripts/release.sh --check       only check that the versions agree and the changelog has an entry
set -euo pipefail
cd "$(dirname "$0")/.."

current() { sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml; }
check() {
  local v; v="$(current)"
  grep -q "__version__ = \"$v\"" libra/__init__.py || { echo "libra/__init__.py doesn't say $v"; exit 1; }
  grep -q "versionName '$v'" android/app/build.gradle || { echo "android/app/build.gradle doesn't say $v"; exit 1; }
  grep -q "<version>$v</version>" clients/java/pom.xml || { echo "clients/java/pom.xml doesn't say $v"; exit 1; }
  grep -q "^## \[$v\]" CHANGELOG.md || { echo "CHANGELOG.md has no '## [$v]' section"; exit 1; }
  echo "version $v is consistent"
}

if [ "${1:-}" = "--check" ]; then check; exit 0; fi
new="${1:?usage: scripts/release.sh X.Y.Z   (or --check)}"
[[ "$new" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must look like 1.2.3"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "commit or stash your changes first"; exit 1; }

old="$(current)"
sed -i "s/^version = \"$old\"/version = \"$new\"/" pyproject.toml
sed -i "s/__version__ = \"$old\"/__version__ = \"$new\"/" libra/__init__.py
code="$(sed -n 's/.*versionCode \([0-9]*\).*/\1/p' android/app/build.gradle)"
sed -i "s/versionCode $code/versionCode $((code + 1))/; s/versionName '.*'/versionName '$new'/" android/app/build.gradle
sed -i "0,/<version>$old<\/version>/s//<version>$new<\/version>/" clients/java/pom.xml
if ! grep -q "^## \[$new\]" CHANGELOG.md; then   # turn "Unreleased" into this release
  sed -i "s/^## \[Unreleased\]/## [Unreleased]\n\n## [$new] - $(date +%F)/" CHANGELOG.md
fi
check

python3 -m unittest discover -s tests
rm -rf dist
if python3 -m build --version >/dev/null 2>&1; then
  python3 -m build
  (cd dist && sha256sum ./* > SHA256SUMS)
  ls -l dist
else
  echo "note: 'pip install build' to build the packages here; the release workflow builds them anyway"
fi

cat <<MSG

Ready. Review the changes (git diff), then publish with:

  git commit -am "Release $new"
  git tag -a v$new -m "Libra $new"
  git push origin HEAD v$new

Pushing the tag builds the packages and the Android APK and creates the GitHub release.
MSG
