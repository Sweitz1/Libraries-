#!/usr/bin/env bash
# Install Libra on Debian (or Ubuntu) as an always-on service you can open from your phone.
#
#   sudo ./deploy/install-debian.sh                 # install or upgrade (keeps your data and token)
#   sudo ./deploy/install-debian.sh --port 8080 --train-at 03:00
#   sudo ./deploy/install-debian.sh --local-only    # only this machine (use with a reverse proxy)
#   sudo ./deploy/install-debian.sh --uninstall     # remove services; data stays in /var/lib/libra
#
# What it sets up:
#   /opt/libra            program (a Python virtual environment)
#   /var/lib/libra        your data: workspace, models, datasets, settings (owned by user "libra")
#   /etc/libra/token      access token for the app and API (readable by root and "libra" only)
#   libra.service         the app + API, restarted automatically, starts at boot
#   libra-auto.timer      nightly "libra auto run" (switched off in the config until you pick sources)
#   /usr/local/bin/libra-admin   run any libra command as the service user, e.g. libra-admin kb add ~/notes
set -euo pipefail

PORT=8000
HOST=0.0.0.0
TRAIN_AT=02:30
TRAIN=1
APT=1
UNINSTALL=0
ROOT=""            # test mode: install under this directory, skip apt, users and systemctl
SVC_USER=libra

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --local-only) HOST=127.0.0.1; shift ;;
    --train-at) TRAIN_AT="$2"; shift 2 ;;
    --no-train) TRAIN=0; shift ;;
    --no-apt) APT=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --root) ROOT="$2"; APT=0; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || { echo "bad --port" >&2; exit 1; }
[[ "$TRAIN_AT" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "--train-at must be HH:MM (24h)" >&2; exit 1; }

PREFIX=/opt/libra
DATA=/var/lib/libra
ETC=/etc/libra
UNITS=/etc/systemd/system
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
r() { printf '%s%s' "$ROOT" "$1"; }   # path under the install root
run_systemctl() { if [ -z "$ROOT" ]; then systemctl "$@"; else echo "(test mode) systemctl $*"; fi; }
as_user() { if [ -z "$ROOT" ]; then runuser -u "$SVC_USER" -- "$@"; else "$@"; fi; }

if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  echo "please run with sudo: sudo $0 $*" >&2; exit 1
fi

if [ "$UNINSTALL" = 1 ]; then
  run_systemctl disable --now libra.service libra-auto.timer 2>/dev/null || true
  rm -f "$(r $UNITS)/libra.service" "$(r $UNITS)/libra-auto.service" "$(r $UNITS)/libra-auto.timer" \
        "$(r /usr/local/bin/libra-admin)"
  run_systemctl daemon-reload || true
  rm -rf "$(r $PREFIX)"
  echo "Libra removed. Your data and token are still in $DATA and $ETC (delete them yourself if you want)."
  exit 0
fi

echo "==> installing Libra from $SRC_DIR"
if [ "$APT" = 1 ]; then
  if ! command -v apt-get >/dev/null; then echo "this script is for Debian/Ubuntu (apt-get not found)" >&2; exit 1; fi
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv python3-pip ca-certificates >/dev/null
fi
python3 - <<'EOF' || { echo "Libra needs Python 3.9 or newer" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
EOF

if [ -z "$ROOT" ] && ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$SVC_USER"
fi
mkdir -p "$(r $PREFIX)" "$(r $DATA)/workspace" "$(r $DATA)/home" "$(r $ETC)" "$(r $UNITS)" "$(r /usr/local/bin)"

echo "==> copying the program to $PREFIX/src and creating its environment"
rm -rf "$(r $PREFIX)/src"
mkdir -p "$(r $PREFIX)/src"
tar -C "$SRC_DIR" --exclude=.git --exclude=checkpoints --exclude=datasets --exclude='__pycache__' \
    --exclude='*.egg-info' -cf - . | tar -C "$(r $PREFIX)/src" -xf -
[ -x "$(r $PREFIX)/venv/bin/python" ] || python3 -m venv "$(r $PREFIX)/venv"
"$(r $PREFIX)/venv/bin/pip" install -q --upgrade pip
"$(r $PREFIX)/venv/bin/pip" install -q "$(r $PREFIX)/src"
LIBRA="$(r $PREFIX)/venv/bin/libra"

if [ ! -s "$(r $ETC)/token" ]; then
  echo "==> creating an access token"
  "$(r $PREFIX)/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(24))' > "$(r $ETC)/token"
fi
chmod 0640 "$(r $ETC)/token"

cat > "$(r $ETC)/libra.env" <<EOF
# Settings for libra.service and libra-auto.service (systemctl restart libra after changes)
LIBRA_HOME=$DATA/home
LIBRA_HOST=$HOST
LIBRA_PORT=$PORT
EOF

if [ ! -f "$(r $DATA)/libra-auto.json" ]; then
  echo "==> writing the training config (switched off until you choose sources)"
  (cd "$(r $DATA)" && "$LIBRA" auto init libra-auto.json >/dev/null)
  "$(r $PREFIX)/venv/bin/python" - "$(r $DATA)/libra-auto.json" <<'EOF'
import json, sys
p = sys.argv[1]; c = json.load(open(p))
c["enabled"] = False
c["sources"] = [{"url": "https://example.com/replace-me"}]
json.dump(c, open(p, "w"), indent=2)
EOF
fi

if [ -z "$ROOT" ]; then
  chown -R "$SVC_USER:$SVC_USER" "$DATA"
  chown "root:$SVC_USER" "$ETC/token"
fi

HARDEN='NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ReadWritePaths='"$DATA"'
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictSUIDSGID=yes
LockPersonality=yes
RestrictRealtime=yes
UMask=0077'

cat > "$(r $UNITS)/libra.service" <<EOF
[Unit]
Description=Libra: app and API (language model, your files, translation, skills)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SVC_USER
Group=$SVC_USER
EnvironmentFile=$ETC/libra.env
WorkingDirectory=$DATA
ExecStart=$PREFIX/venv/bin/libra serve --host \${LIBRA_HOST} --port \${LIBRA_PORT} --token-file $ETC/token --model $DATA/checkpoints/auto --workspace $DATA/workspace --auto-config $DATA/libra-auto.json
Restart=on-failure
RestartSec=5
$HARDEN

[Install]
WantedBy=multi-user.target
EOF

cat > "$(r $UNITS)/libra-auto.service" <<EOF
[Unit]
Description=Libra: one unattended training run
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=$SVC_USER
Group=$SVC_USER
EnvironmentFile=$ETC/libra.env
WorkingDirectory=$DATA
ExecStart=$PREFIX/venv/bin/libra auto run $DATA/libra-auto.json --quiet
Nice=10
IOSchedulingClass=idle
CPUSchedulingPolicy=batch
$HARDEN
EOF

cat > "$(r $UNITS)/libra-auto.timer" <<EOF
[Unit]
Description=Libra: nightly training run

[Timer]
OnCalendar=*-*-* $TRAIN_AT:00
Persistent=true
RandomizedDelaySec=10min

[Install]
WantedBy=timers.target
EOF

cat > "$(r /usr/local/bin)/libra-admin" <<EOF
#!/bin/sh
# Run a libra command as the service user, with the service's data. Example: libra-admin kb add /path/to/notes
cd $DATA || exit 1
if [ "\$(id -u)" -eq 0 ]; then
  exec runuser -u $SVC_USER -- env LIBRA_HOME=$DATA/home $PREFIX/venv/bin/libra "\$@"
fi
exec sudo -u $SVC_USER env LIBRA_HOME=$DATA/home $PREFIX/venv/bin/libra "\$@"
EOF
chmod 0755 "$(r /usr/local/bin)/libra-admin"

run_systemctl daemon-reload
run_systemctl enable --now libra.service
run_systemctl restart libra.service
if [ "$TRAIN" = 1 ]; then run_systemctl enable --now libra-auto.timer; fi

TOKEN="$(cat "$(r $ETC)/token")"
IPS="$(hostname -I 2>/dev/null || true)"
echo
echo "==> Libra is installed."
if [ "$HOST" = "127.0.0.1" ]; then
  echo "   It listens on this machine only (port $PORT). Put a reverse proxy in front: see deploy/README.md"
else
  echo "   Open on your phone (same Wi-Fi):"
  for ip in $IPS; do case "$ip" in *:*) ;; *) echo "     http://$ip:$PORT/#token=$TOKEN" ;; esac; done
fi
echo "   API for OpenAI-compatible apps (e.g. Nexus Agent): http://<this-ip>:$PORT/v1, API key = the token"
echo "   Token: $ETC/token     Logs: journalctl -u libra -f"
echo "   Add files:   sudo libra-admin kb add /path/to/files     (copy them somewhere the 'libra' user can read)"
echo "   Training:    edit $DATA/libra-auto.json (sources, then \"enabled\": true); runs nightly at $TRAIN_AT"
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
  echo "   Firewall (ufw) is on. Allow your home network, for example:"
  echo "     sudo ufw allow from 192.168.0.0/16 to any port $PORT proto tcp"
fi
