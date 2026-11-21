#!/usr/bin/env bash
# Install Libra on Debian (or Ubuntu / Raspberry Pi OS) as an always-on service you can open from your phone.
#
#   sudo ./deploy/install-debian.sh                    # install or upgrade (keeps your data and token)
#   sudo ./deploy/install-debian.sh --tailscale        # also reach it from anywhere, with HTTPS, via Tailscale
#   sudo ./deploy/install-debian.sh --tailscale-only   # only via Tailscale (nothing open on your home network)
#   sudo ./deploy/install-debian.sh --port 8080 --train-at 03:00
#   sudo ./deploy/install-debian.sh --local-only       # only this machine (use with a reverse proxy)
#   sudo ./deploy/install-debian.sh --uninstall        # remove services; data stays in /var/lib/libra
#
# Other options: --ts-hostname NAME (default libra), --no-mdns, --no-train, --no-agent, --no-apt.
# For an unattended Tailscale login, pass a key in the environment: sudo TS_AUTHKEY=tskey-… ./install-debian.sh --tailscale
# (the key is used once and never written to disk).
#
# What it sets up:
#   /opt/libra             program (a Python virtual environment)
#   /var/lib/libra         your data: workspace, models, datasets, goals, settings (owned by user "libra")
#   /etc/libra/token       access token for the app and API (readable by root and "libra" only)
#   libra.service          the app + API; starts at boot (after Tailscale, if used) and restarts if it stops
#   libra-auto.timer       nightly "libra auto run" (off in its config until you pick sources); failed runs retry
#   libra-agent.timer      runs your scheduled agent goals (checked every 5 minutes)
#   <hostname>.local       the server's name on your Wi-Fi (via avahi), so phones can find it without an IP
#   libra-admin            run any libra command as the service, e.g. sudo libra-admin kb add /path/to/notes
set -euo pipefail

PORT=8000
HOST=0.0.0.0
TRAIN_AT=02:30
TRAIN=1
AGENT=1
APT=1
MDNS=1
TAILSCALE=0
TS_ONLY=0
TS_HOST=libra
UNINSTALL=0
ROOT=""            # test mode: install under this directory, skip apt, users, tailscale and systemctl
SVC_USER=libra

usage() { sed -n '2,28p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --local-only) HOST=127.0.0.1; MDNS=0; shift ;;
    --tailscale) TAILSCALE=1; shift ;;
    --tailscale-only) TAILSCALE=1; TS_ONLY=1; HOST=127.0.0.1; MDNS=0; shift ;;
    --ts-hostname) TS_HOST="$2"; shift 2 ;;
    --train-at) TRAIN_AT="$2"; shift 2 ;;
    --no-train) TRAIN=0; shift ;;
    --no-agent) AGENT=0; shift ;;
    --no-mdns) MDNS=0; shift ;;
    --no-apt) APT=0; shift ;;
    --uninstall) UNINSTALL=1; shift ;;
    --root) ROOT="$2"; APT=0; shift 2 ;;
    -h|--help) usage 0 ;;
    *) echo "unknown option: $1" >&2; usage 1 ;;
  esac
done
[[ "$PORT" =~ ^[0-9]+$ ]] && [ "$PORT" -ge 1 ] && [ "$PORT" -le 65535 ] || { echo "bad --port" >&2; exit 1; }
[[ "$TRAIN_AT" =~ ^([01][0-9]|2[0-3]):[0-5][0-9]$ ]] || { echo "--train-at must be HH:MM (24h)" >&2; exit 1; }
[[ "$TS_HOST" =~ ^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$ ]] || { echo "--ts-hostname: lowercase letters, digits, -" >&2; exit 1; }

PREFIX=/opt/libra
DATA=/var/lib/libra
ETC=/etc/libra
UNITS=/etc/systemd/system
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
r() { printf '%s%s' "$ROOT" "$1"; }   # path under the install root
run_systemctl() { if [ -z "$ROOT" ]; then systemctl "$@"; else echo "(test mode) systemctl $*"; fi; }
say() { echo "==> $*"; }

if [ -z "$ROOT" ] && [ "$(id -u)" -ne 0 ]; then
  echo "please run with sudo: sudo $0 $*" >&2; exit 1
fi

if [ "$UNINSTALL" = 1 ]; then
  run_systemctl disable --now libra.service libra-auto.timer libra-agent.timer 2>/dev/null || true
  rm -f "$(r $UNITS)"/libra.service "$(r $UNITS)"/libra-auto.{service,timer} "$(r $UNITS)"/libra-agent.{service,timer} \
        "$(r /usr/local/bin/libra-admin)" "$(r /etc/avahi/services/libra.service)"
  run_systemctl daemon-reload || true
  rm -rf "$(r $PREFIX)"
  echo "Libra removed. Your data and token are still in $DATA and $ETC (delete them yourself if you want)."
  if command -v tailscale >/dev/null 2>&1; then
    echo "Tailscale is still installed; to stop serving Libra over it: sudo tailscale serve reset"
  fi
  exit 0
fi

say "installing Libra from $SRC_DIR"
if [ "$APT" = 1 ]; then
  if ! command -v apt-get >/dev/null; then echo "this script is for Debian/Ubuntu (apt-get not found)" >&2; exit 1; fi
  export DEBIAN_FRONTEND=noninteractive
  pkgs="python3 python3-venv python3-pip ca-certificates"
  [ "$MDNS" = 1 ] && pkgs="$pkgs avahi-daemon"
  [ "$TAILSCALE" = 1 ] && pkgs="$pkgs curl"
  apt-get update -qq
  # shellcheck disable=SC2086
  apt-get install -y -qq $pkgs >/dev/null
fi
python3 - <<'EOF' || { echo "Libra needs Python 3.9 or newer" >&2; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)
EOF

# ------------------------------------------------------------- Tailscale
TS_NAME=""
TS_HTTPS=0
if [ "$TAILSCALE" = 1 ]; then
  if [ -n "$ROOT" ]; then
    echo "(test mode) would install and set up Tailscale as '$TS_HOST'"
  else
    if ! command -v tailscale >/dev/null 2>&1; then
      say "installing Tailscale from its official package repository"
      # shellcheck disable=SC1091
      . /etc/os-release
      dist="${ID:-debian}"; codename="${VERSION_CODENAME:-}"
      case "$dist" in debian|ubuntu|raspbian) ;; *) dist=debian ;; esac
      if [ -z "$codename" ]; then echo "can't tell your release name; install Tailscale yourself: https://tailscale.com/download" >&2; exit 1; fi
      curl -fsSL "https://pkgs.tailscale.com/stable/$dist/$codename.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
      curl -fsSL "https://pkgs.tailscale.com/stable/$dist/$codename.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list
      apt-get update -qq
      apt-get install -y -qq tailscale >/dev/null
    fi
    systemctl enable --now tailscaled >/dev/null
    if ! tailscale status >/dev/null 2>&1; then
      say "signing this machine in to Tailscale"
      if [ -n "${TS_AUTHKEY:-}" ]; then
        tailscale up --authkey="$TS_AUTHKEY" --hostname="$TS_HOST"
      else
        echo "   Open the link below on any device, sign in, and approve this machine. Waiting…"
        tailscale up --hostname="$TS_HOST" --timeout=15m
      fi
    fi
    TS_NAME="$(tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))' 2>/dev/null || true)"
    say "publishing Libra over HTTPS inside your Tailscale network"
    if timeout 60 tailscale serve --bg "$PORT" </dev/null >/dev/null 2>&1; then
      TS_HTTPS=1
    else
      echo "   HTTPS isn't enabled for your Tailscale network yet. In the Tailscale admin console, open DNS and"
      echo "   enable MagicDNS and HTTPS Certificates, then run:  sudo tailscale serve --bg $PORT"
      if [ "$TS_ONLY" = 1 ]; then
        echo "   (--tailscale-only needs HTTPS serving; until then Libra is reachable only on this machine)"
      fi
    fi
  fi
fi

# ---------------------------------------------------------- the program
if [ -z "$ROOT" ] && ! id "$SVC_USER" >/dev/null 2>&1; then
  useradd --system --home-dir "$DATA" --shell /usr/sbin/nologin "$SVC_USER"
fi
mkdir -p "$(r $PREFIX)" "$(r $DATA)/workspace" "$(r $DATA)/home" "$(r $ETC)" "$(r $UNITS)" "$(r /usr/local/bin)"

say "copying the program to $PREFIX/src and creating its environment"
rm -rf "$(r $PREFIX)/src"
mkdir -p "$(r $PREFIX)/src"
tar -C "$SRC_DIR" --exclude=.git --exclude=checkpoints --exclude=datasets --exclude='__pycache__' \
    --exclude='*.egg-info' -cf - . | tar -C "$(r $PREFIX)/src" -xf -
[ -x "$(r $PREFIX)/venv/bin/python" ] || python3 -m venv "$(r $PREFIX)/venv"
"$(r $PREFIX)/venv/bin/pip" install -q --upgrade pip
"$(r $PREFIX)/venv/bin/pip" install -q "$(r $PREFIX)/src"
LIBRA="$(r $PREFIX)/venv/bin/libra"

if [ ! -s "$(r $ETC)/token" ]; then
  say "creating an access token"
  "$(r $PREFIX)/venv/bin/python" -c 'import secrets; print(secrets.token_urlsafe(24))' > "$(r $ETC)/token"
fi
chmod 0640 "$(r $ETC)/token"

cat > "$(r $ETC)/libra.env" <<EOF
# Settings for the Libra services (sudo systemctl restart libra after changes)
LIBRA_HOME=$DATA/home
LIBRA_HOST=$HOST
LIBRA_PORT=$PORT
EOF

if [ ! -f "$(r $DATA)/libra-auto.json" ]; then
  say "writing the training config (switched off until you choose sources)"
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

# ------------------------------------------------------------- services
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
COMMON="User=$SVC_USER
Group=$SVC_USER
EnvironmentFile=$ETC/libra.env
WorkingDirectory=$DATA"

cat > "$(r $UNITS)/libra.service" <<EOF
[Unit]
Description=Libra: app and API (language model, your files, translation, skills, agent)
After=network-online.target tailscaled.service avahi-daemon.service
Wants=network-online.target

[Service]
Type=simple
$COMMON
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
# a run that fails (network down, disk full…) is retried up to 3 times, 20 minutes apart
StartLimitIntervalSec=6h
StartLimitBurst=4

[Service]
Type=oneshot
$COMMON
ExecStart=$PREFIX/venv/bin/libra auto run $DATA/libra-auto.json --quiet
Restart=on-failure
RestartSec=20min
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

cat > "$(r $UNITS)/libra-agent.service" <<EOF
[Unit]
Description=Libra: run scheduled agent goals that are due
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
$COMMON
ExecStart=$PREFIX/venv/bin/libra agent run-due
Nice=5
$HARDEN
EOF

cat > "$(r $UNITS)/libra-agent.timer" <<EOF
[Unit]
Description=Libra: check for due agent goals every 5 minutes

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

if [ "$MDNS" = 1 ]; then
  mkdir -p "$(r /etc/avahi/services)"
  cat > "$(r /etc/avahi/services/libra.service)" <<EOF
<?xml version="1.0" standalone='no'?>
<!DOCTYPE service-group SYSTEM "avahi-service.dtd">
<service-group>
  <name replace-wildcards="yes">Libra on %h</name>
  <service>
    <type>_http._tcp</type>
    <port>$PORT</port>
    <txt-record>path=/</txt-record>
  </service>
</service-group>
EOF
  run_systemctl enable --now avahi-daemon.service >/dev/null 2>&1 || true
fi

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
if [ "$AGENT" = 1 ]; then run_systemctl enable --now libra-agent.timer; fi

# ---------------------------------------------------------------- done
echo
say "Libra is installed and starts automatically at boot."
PAIR_ARGS=(--port "$PORT" --token-file "$(r $ETC)/token")
if [ "$TS_HTTPS" = 1 ] && [ -n "$TS_NAME" ]; then PAIR_ARGS+=(--url "https://$TS_NAME"); fi
if [ "$TS_ONLY" = 0 ] && [ "$HOST" != "127.0.0.1" ]; then
  if [ "$MDNS" = 1 ]; then PAIR_ARGS+=(--url "http://$(hostname -s 2>/dev/null || hostname).local:$PORT"); fi
  for ip in $(hostname -I 2>/dev/null || true); do case "$ip" in *:*) ;; *) PAIR_ARGS+=(--url "http://$ip:$PORT") ;; esac; done
fi
if [ "${#PAIR_ARGS[@]}" -gt 4 ]; then
  echo
  "$LIBRA" pair "${PAIR_ARGS[@]}" || true
else
  echo "   It listens on this machine only (port $PORT). See deploy/README.md for a reverse proxy or Tailscale."
fi
echo
echo "   Show this QR code again any time:   sudo libra-admin pair --token-file $ETC/token"
echo "   OpenAI-compatible apps (Nexus Agent): base URL = one of the addresses above + /v1, API key = the token"
echo "   Logs: journalctl -u libra -f        Token: $ETC/token"
echo "   Training: edit $DATA/libra-auto.json (sources, then \"enabled\": true); runs nightly at $TRAIN_AT"
if [ "$TAILSCALE" = 1 ] && [ -z "$ROOT" ]; then
  echo "   Tailscale: install the Tailscale app on your phone and sign in with the same account."
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active" && [ "$HOST" != "127.0.0.1" ]; then
  echo "   Firewall (ufw) is on. Allow your home network, for example:"
  echo "     sudo ufw allow from 192.168.0.0/16 to any port $PORT proto tcp"
  [ "$MDNS" = 1 ] && echo "     sudo ufw allow 5353/udp   # lets phones find $(hostname -s 2>/dev/null || hostname).local"
fi
