#!/usr/bin/env bash
# Build a Debian package (.deb) for Libra, from the repository, with dpkg-deb.
#
#   bash packaging/build-deb.sh            # builds dist/libra_<version>_all.deb
#   bash packaging/build-deb.sh --out DIR  # write the .deb into DIR
#
# Libra is pure Python (NumPy + the standard library), so the package is Architecture: all and depends on
# the system python3 and python3-numpy — no compiling, no network at install time. It installs the code to
# /usr/share/libra, a /usr/bin/libra launcher, and the systemd units; the postinst creates the "libra"
# service user and data, enables the service, and installs+trusts the plugin suite (same layout as
# deploy/install-debian.sh). Install the result with:  sudo apt install ./libra_<version>_all.deb
#
# Only dpkg-deb is required to build (no debhelper/fakeroot).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$SRC/dist"
while [ $# -gt 0 ]; do case "$1" in --out) OUT="$2"; shift 2 ;; *) echo "unknown option: $1" >&2; exit 2 ;; esac; done

command -v dpkg-deb >/dev/null || { echo "dpkg-deb is required (apt-get install dpkg-dev)" >&2; exit 1; }
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' "$SRC/pyproject.toml" | head -1)"
[ -n "$VERSION" ] || { echo "could not read version from pyproject.toml" >&2; exit 1; }
ARCH=all
PKG="libra"
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
say() { echo "==> $*"; }

# ------------------------------------------------------------------ payload
say "staging files for $PKG $VERSION"
install -d "$STAGE/usr/share/libra" "$STAGE/usr/bin" "$STAGE/lib/systemd/system" \
          "$STAGE/usr/share/doc/libra" "$STAGE/etc/avahi/services" "$STAGE/DEBIAN"
# the code: the libra package and the bundled plugins (pure Python + data files)
cp -r "$SRC/libra" "$STAGE/usr/share/libra/"
cp -r "$SRC/plugins" "$STAGE/usr/share/libra/"
find "$STAGE/usr/share/libra" -name '__pycache__' -type d -prune -exec rm -rf {} +
cp "$SRC/README.md" "$SRC/INSTALL.md" "$SRC/CHANGELOG.md" "$STAGE/usr/share/doc/libra/" 2>/dev/null || true

# launcher: run the module from /usr/share/libra against the system python + numpy
cat > "$STAGE/usr/bin/libra" <<'EOF'
#!/bin/sh
# Libra command-line launcher (installed by the libra Debian package).
export PYTHONPATH="/usr/share/libra${PYTHONPATH:+:$PYTHONPATH}"
export PYTHONDONTWRITEBYTECODE=1   # keep /usr/share/libra read-only-clean (no __pycache__)
exec /usr/bin/python3 -m libra "$@"
EOF
chmod 0755 "$STAGE/usr/bin/libra"

# libra-admin: run a command as the service user with the service's data
cat > "$STAGE/usr/bin/libra-admin" <<'EOF'
#!/bin/sh
# Run a libra command as the service user. Example: sudo libra-admin kb add /path/to/notes
cd /var/lib/libra || exit 1
if [ "$(id -u)" -eq 0 ]; then
  exec runuser -u libra -- env LIBRA_HOME=/var/lib/libra/home /usr/bin/libra "$@"
fi
exec sudo -u libra env LIBRA_HOME=/var/lib/libra/home /usr/bin/libra "$@"
EOF
chmod 0755 "$STAGE/usr/bin/libra-admin"

# ------------------------------------------------------------------ units
HARDEN='NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths=/var/lib/libra
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes
UMask=0077'
COMMON='User=libra
Group=libra
EnvironmentFile=/etc/libra/libra.env
WorkingDirectory=/var/lib/libra'

cat > "$STAGE/lib/systemd/system/libra.service" <<EOF
[Unit]
Description=Libra: app and API (language model, your files, translation, skills, agent)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
$COMMON
ExecStart=/usr/bin/libra serve --host \${LIBRA_HOST} --port \${LIBRA_PORT} --token-file /etc/libra/token --model /var/lib/libra/checkpoints/auto --workspace /var/lib/libra/workspace --auto-config /var/lib/libra/libra-auto.json
Restart=on-failure
RestartSec=5
$HARDEN

[Install]
WantedBy=multi-user.target
EOF

cat > "$STAGE/lib/systemd/system/libra-auto.service" <<EOF
[Unit]
Description=Libra: one unattended training run
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=6h
StartLimitBurst=4

[Service]
Type=oneshot
$COMMON
ExecStart=/usr/bin/libra auto run /var/lib/libra/libra-auto.json --quiet
Restart=on-failure
RestartSec=20min
Nice=10
IOSchedulingClass=idle
CPUSchedulingPolicy=batch
$HARDEN
EOF

cat > "$STAGE/lib/systemd/system/libra-auto.timer" <<EOF
[Unit]
Description=Libra: nightly training run
[Timer]
OnCalendar=*-*-* 02:30:00
Persistent=true
RandomizedDelaySec=10min
[Install]
WantedBy=timers.target
EOF

cat > "$STAGE/lib/systemd/system/libra-agent.service" <<EOF
[Unit]
Description=Libra: run scheduled agent goals that are due
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
$COMMON
ExecStart=/usr/bin/libra agent run-due
Nice=5
$HARDEN
EOF

cat > "$STAGE/lib/systemd/system/libra-agent.timer" <<EOF
[Unit]
Description=Libra: check for due agent goals every 5 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
[Install]
WantedBy=timers.target
EOF

cat > "$STAGE/lib/systemd/system/libra-codex.service" <<EOF
[Unit]
Description=Libra: codex lore-wiki website (local; MediaWiki-style api.php)
After=network-online.target libra.service
Wants=network-online.target
[Service]
Type=simple
$COMMON
ExecStart=/usr/bin/libra skill --yes run codex_serve host=127.0.0.1 port=8800
Restart=on-failure
RestartSec=10
$HARDEN
[Install]
WantedBy=multi-user.target
EOF

cat > "$STAGE/lib/systemd/system/libra-discord.service" <<EOF
[Unit]
Description=Libra: Discord bot
After=network-online.target libra.service
Wants=network-online.target
# Stays dormant until a token exists. Add one with:  sudo libra-admin discord setup
ConditionPathExists=/var/lib/libra/home/discord-token
StartLimitIntervalSec=10min
StartLimitBurst=5
[Service]
Type=simple
$COMMON
ExecStart=/usr/bin/libra discord run --token-file /var/lib/libra/home/discord-token --workspace /var/lib/libra/workspace --model /var/lib/libra/checkpoints/auto --auto-config /var/lib/libra/libra-auto.json
Restart=on-failure
RestartSec=30
$HARDEN
[Install]
WantedBy=multi-user.target
EOF

cat > "$STAGE/etc/avahi/services/libra.service" <<'EOF'
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Libra on %h</name>
  <service><type>_http._tcp</type><port>8000</port><txt-record>path=/</txt-record></service>
</service-group>
EOF

# ------------------------------------------------------------------ desktop integration
# Applications-menu launchers and the "libra" icon, so Libra appears in the desktop
# menu (Raspberry Pi OS Desktop, GNOME, KDE, ...) and opens the web app in a browser.
say "staging desktop launchers and icons"
install -d "$STAGE/usr/share/applications"
install -m 0644 "$SRC/deploy/desktop/libra.desktop"       "$STAGE/usr/share/applications/libra.desktop"
install -m 0644 "$SRC/deploy/desktop/libra-codex.desktop" "$STAGE/usr/share/applications/libra-codex.desktop"
for pair in "favicon-32.png:32x32" "icon-192.png:192x192" "icon-512.png:512x512"; do
  png="${pair%%:*}"; size="${pair##*:}"
  if [ -f "$SRC/libra/webui/$png" ]; then
    install -d "$STAGE/usr/share/icons/hicolor/$size/apps"
    install -m 0644 "$SRC/libra/webui/$png" "$STAGE/usr/share/icons/hicolor/$size/apps/libra.png"
  fi
done
if [ -f "$SRC/libra/webui/icon.svg" ]; then
  install -d "$STAGE/usr/share/icons/hicolor/scalable/apps"
  install -m 0644 "$SRC/libra/webui/icon.svg" "$STAGE/usr/share/icons/hicolor/scalable/apps/libra.svg"
fi

# ------------------------------------------------------------------ control
INSTALLED_KB=$(du -sk "$STAGE" | cut -f1)
cat > "$STAGE/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Architecture: $ARCH
Maintainer: Libra packaging <libra@localhost>
Installed-Size: $INSTALLED_KB
Depends: python3 (>= 3.9), python3-numpy, adduser
Recommends: avahi-daemon, tailscale
Section: misc
Priority: optional
Homepage: https://github.com/Sweitz1/Libraries-
Description: Self-hosted GPT-style model, agent and lore/world engine (from scratch, NumPy only)
 Libra is a GPT-style language model written from scratch on NumPy, with an agent,
 a cited knowledge base, translation, a Discord bot, and a worldbuilding suite
 (lorebook/worldbook extraction from fan wikis, fanfiction tools, Fandom-style
 card rendering, and a codex lore-wiki / world engine with canon + AU continuities).
 No pretrained models, no API keys, nothing leaves the machine. Runs as a systemd
 service you open from your phone or computer.
EOF
cp "$SRC/packaging/postinst" "$STAGE/DEBIAN/postinst"
cp "$SRC/packaging/prerm" "$STAGE/DEBIAN/prerm"
cp "$SRC/packaging/postrm" "$STAGE/DEBIAN/postrm"
chmod 0755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/prerm" "$STAGE/DEBIAN/postrm"

# ------------------------------------------------------------------ build
mkdir -p "$OUT"
DEB="$OUT/${PKG}_${VERSION}_${ARCH}.deb"
say "building $DEB"
dpkg-deb --root-owner-group --build "$STAGE" "$DEB" >/dev/null
say "done:"
dpkg-deb --info "$DEB" | sed 's/^/   /'
echo
echo "Install it on a Raspberry Pi / Debian machine with:"
echo "   sudo apt install $DEB          # pulls python3-numpy automatically"
echo "   # or: sudo dpkg -i $DEB && sudo apt-get -f install"
