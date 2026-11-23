#!/usr/bin/env bash
# Build an APT repository from one or more Libra .deb packages, so users can
#   sudo apt install libra    (and get upgrades with apt)
# instead of downloading each .deb.
#
#   bash packaging/build-apt-repo.sh --out apt-repo path/to/libra_*.deb
#   bash packaging/build-apt-repo.sh --out apt-repo --pool some/dir   # take every .deb in a dir
#   APT_SIGN_KEY=<gpg-key-id> bash packaging/build-apt-repo.sh --out apt-repo libra_*.deb   # signed
#
# The result is a flat, static site you can host anywhere (GitHub Pages, S3, a plain web server):
#   apt-repo/pool/main/<debs>
#   apt-repo/dists/stable/main/binary-all/Packages[.gz]
#   apt-repo/dists/stable/Release        (+ InRelease, Release.gpg and the public key, when signed)
#   apt-repo/index.html                  (install instructions)
#
# Needs dpkg-scanpackages (dpkg-dev), gzip and sha256sum; gpg only when signing.
set -euo pipefail

OUT="apt-repo"; POOL_DIR=""; DEBS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --out) OUT="$2"; shift 2 ;;
    --pool) POOL_DIR="$2"; shift 2 ;;
    *.deb) DEBS+=("$1"); shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
command -v dpkg-scanpackages >/dev/null || { echo "dpkg-scanpackages is required (apt-get install dpkg-dev)" >&2; exit 1; }

SUITE=stable
BASE_URL="${APT_BASE_URL:-}"   # optional, only for the landing page's example command
say() { echo "==> $*"; }

rm -rf "$OUT"
install -d "$OUT/pool/main" "$OUT/dists/$SUITE/main/binary-all"

# collect the .deb files
if [ -n "$POOL_DIR" ]; then
  while IFS= read -r f; do DEBS+=("$f"); done < <(find "$POOL_DIR" -name '*.deb' -type f)
fi
[ "${#DEBS[@]}" -gt 0 ] || { echo "no .deb files given (pass paths, or --pool DIR)" >&2; exit 1; }
for d in "${DEBS[@]}"; do cp -f "$d" "$OUT/pool/main/"; done
say "included $(ls "$OUT/pool/main" | wc -l) package file(s)"

# the Packages index (Filename is relative to the repo root, which is what apt fetches)
( cd "$OUT" && dpkg-scanpackages --arch all pool/main /dev/null > dists/$SUITE/main/binary-all/Packages 2>/dev/null )
gzip -9kf "$OUT/dists/$SUITE/main/binary-all/Packages"

# the Release file, with the Packages hashes (paths relative to dists/stable)
rel="$OUT/dists/$SUITE/Release"
{
  echo "Origin: Libra"
  echo "Label: Libra"
  echo "Suite: $SUITE"
  echo "Codename: $SUITE"
  echo "Architectures: all"
  echo "Components: main"
  echo "Date: $(date -Ru)"
  echo "Description: Libra APT repository (self-hosted GPT-style model, agent and world engine)"
} > "$rel"
hashes() {  # $1 = algo field name, $2 = command
  echo "$1:"
  ( cd "$OUT/dists/$SUITE" && for f in main/binary-all/Packages main/binary-all/Packages.gz; do
      printf ' %s %d %s\n' "$($2 "$f" | cut -d' ' -f1)" "$(stat -c%s "$f")" "$f"
    done )
}
hashes MD5Sum md5sum >> "$rel"
hashes SHA256 sha256sum >> "$rel"

# optional signing (apt trusts a signed repo without [trusted=yes])
if [ -n "${APT_SIGN_KEY:-}" ]; then
  command -v gpg >/dev/null || { echo "gpg required to sign (APT_SIGN_KEY set)" >&2; exit 1; }
  say "signing the Release with key $APT_SIGN_KEY"
  gpg --batch --yes --default-key "$APT_SIGN_KEY" --clearsign -o "$OUT/dists/$SUITE/InRelease" "$rel"
  gpg --batch --yes --default-key "$APT_SIGN_KEY" -abs -o "$OUT/dists/$SUITE/Release.gpg" "$rel"
  gpg --export "$APT_SIGN_KEY" > "$OUT/libra-archive-keyring.gpg"
  SIGNED=1
else
  say "no APT_SIGN_KEY: building an UNSIGNED repo (install needs [trusted=yes])"
  SIGNED=0
fi

# a small landing page with copy-paste install instructions
url="${BASE_URL:-https://<your-host>/apt}"
if [ "$SIGNED" = 1 ]; then
  src="deb [signed-by=/usr/share/keyrings/libra.gpg arch=all] $url $SUITE main"
  setup="curl -fsSL $url/libra-archive-keyring.gpg | sudo tee /usr/share/keyrings/libra.gpg >/dev/null
echo \"$src\" | sudo tee /etc/apt/sources.list.d/libra.list"
else
  src="deb [trusted=yes arch=all] $url $SUITE main"
  setup="echo \"$src\" | sudo tee /etc/apt/sources.list.d/libra.list"
fi
cat > "$OUT/index.html" <<HTML
<!doctype html><meta charset=utf-8><title>Libra APT repository</title>
<style>body{font-family:system-ui,Segoe UI,Arial,sans-serif;max-width:760px;margin:40px auto;padding:0 20px;line-height:1.5}
pre{background:#0d1117;color:#e6edf3;padding:14px;border-radius:8px;overflow:auto}</style>
<h1>Libra APT repository</h1>
<p>Install Libra on a Raspberry Pi, Debian or Ubuntu, and get upgrades with <code>apt</code>:</p>
<pre>$setup
sudo apt update
sudo apt install libra</pre>
<p>Then pair a device: <code>sudo libra-admin pair --token-file /etc/libra/token --name "my phone"</code>.
Upgrade later with <code>sudo apt update &amp;&amp; sudo apt upgrade</code>.</p>
<p>Source and docs: <a href="https://github.com/Sweitz1/Libraries-">github.com/Sweitz1/Libraries-</a></p>
HTML

say "done. repo at: $OUT"
echo "   Packages:"; grep -E '^(Package|Version|Architecture):' "$OUT/dists/$SUITE/main/binary-all/Packages" | sed 's/^/     /'
echo "   Install line: $src"
