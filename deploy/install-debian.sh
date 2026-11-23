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
#   sudo ./deploy/install-debian.sh --discord          # also run Libra as a Discord bot (asks for the bot token)
#   sudo ./deploy/install-debian.sh --full             # the whole suite: plugins + the codex lore-wiki site
#   sudo ./deploy/install-debian.sh --codex            # also run the codex lore-wiki website (local only)
#
# The plugin suite (worldbuilding/lorebook, fanfiction, cards, codex, PDF/Office/media ingest) installs by
# default and is trusted for the service; pass --no-plugins to skip it. The codex site is opt-in (--codex),
# binds to 127.0.0.1 with no auth (reach it over SSH or Tailscale); --codex-host / --codex-port override.
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
#   libra-discord.service  the Discord bot (with --discord; token in /etc/libra/discord-token)
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
DISCORD=0
PLUGINS=1          # install and trust the plugin suite for the service
CODEX=0            # run the codex lore-wiki website as a service
CODEX_HOST=127.0.0.1
CODEX_PORT=8800
UNINSTALL=0
ROOT=""            # test mode: install under this directory, skip apt, users, tailscale and systemctl
SVC_USER=libra

usage() { sed -n '2,32p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
while [ $# -gt 0 ]; do
  case "$1" in
    --port) PORT="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --local-only) HOST=127.0.0.1; MDNS=0; shift ;;
    --tailscale) TAILSCALE=1; shift ;;
    --tailscale-only) TAILSCALE=1; TS_ONLY=1; HOST=127.0.0.1; MDNS=0; shift ;;
    --ts-hostname) TS_HOST="$2"; shift 2 ;;
    --discord) DISCORD=1; shift ;;
    --plugins) PLUGINS=1; shift ;;
    --no-plugins) PLUGINS=0; shift ;;
    --codex) CODEX=1; PLUGINS=1; shift ;;
    --codex-host) CODEX_HOST="$2"; CODEX=1; PLUGINS=1; shift 2 ;;
    --codex-port) CODEX_PORT="$2"; CODEX=1; PLUGINS=1; shift 2 ;;
    --full) PLUGINS=1; CODEX=1; shift ;;
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
[[ "$CODEX_PORT" =~ ^[0-9]+$ ]] && [ "$CODEX_PORT" -ge 1 ] && [ "$CODEX_PORT" -le 65535 ] || { echo "bad --codex-port" >&2; exit 1; }

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
  run_systemctl disable --now libra.service libra-auto.timer libra-agent.timer libra-discord.service libra-codex.service 2>/dev/null || true
  rm -f "$(r $UNITS)"/libra.service "$(r $UNITS)"/libra-auto.{service,timer} "$(r $UNITS)"/libra-agent.{service,timer} \
        "$(r $UNITS)/libra-discord.service" "$(r $UNITS)/libra-codex.service" \
        "$(r /usr/local/bin/libra-admin)" "$(r /etc/avahi/services/libra.service)" \
        "$(r /usr/share/applications/libra.desktop)" "$(r /usr/share/applications/libra-codex.desktop)" \
        "$(r /usr/share/icons/hicolor/32x32/apps/libra.png)" "$(r /usr/share/icons/hicolor/192x192/apps/libra.png)" \
        "$(r /usr/share/icons/hicolor/512x512/apps/libra.png)" "$(r /usr/share/icons/hicolor/scalable/apps/libra.svg)"
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

# ------------------------------------------------------------- plugin suite
# Install the bundled plugins into the service's plugin folder (loaded automatically from LIBRA_HOME/plugins)
# and record trust for each, so the worldbuilding / lorebook / codex / document skills work for the service.
PLUGIN_DIR="$DATA/home/plugins"
if [ "$PLUGINS" = 1 ]; then
  say "installing the plugin suite (worldbuilding, lorebook, fanfiction, cards, codex, document ingest)"
  mkdir -p "$(r $PLUGIN_DIR)"
  cp "$(r $PREFIX)/src/plugins/"*.py "$(r $PLUGIN_DIR)/" 2>/dev/null || true
  if [ -z "$ROOT" ]; then
    chown -R "$SVC_USER:$SVC_USER" "$PLUGIN_DIR"
    for f in "$PLUGIN_DIR"/*.py; do
      [ -e "$f" ] || continue
      runuser -u "$SVC_USER" -- env LIBRA_HOME="$DATA/home" "$LIBRA" trust accept-plugin "$f" >/dev/null 2>&1 || true
    done
  fi
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

if [ "$DISCORD" = 1 ]; then
  if [ ! -s "$(r $ETC)/discord-token" ] && [ -t 0 ] && [ -z "$ROOT" ]; then
    echo "   Discord bot token (Developer Portal -> your application -> Bot -> Reset Token). Typing is hidden:"
    read -rs DTOKEN; echo
    if [ -n "$DTOKEN" ]; then printf '%s\n' "$DTOKEN" > "$(r $ETC)/discord-token"; fi
    unset DTOKEN
  fi
  if [ -s "$(r $ETC)/discord-token" ]; then
    chmod 0640 "$(r $ETC)/discord-token"
    if [ -z "$ROOT" ]; then chown "root:$SVC_USER" "$ETC/discord-token"; fi
  fi
  cat > "$(r $UNITS)/libra-discord.service" <<EOF
[Unit]
Description=Libra: Discord bot
After=network-online.target libra.service
Wants=network-online.target
ConditionPathExists=$ETC/discord-token
StartLimitIntervalSec=10min
StartLimitBurst=5

[Service]
Type=simple
$COMMON
ExecStart=$PREFIX/venv/bin/libra discord run --token-file $ETC/discord-token --workspace $DATA/workspace --model $DATA/checkpoints/auto --auto-config $DATA/libra-auto.json
Restart=on-failure
RestartSec=30
$HARDEN

[Install]
WantedBy=multi-user.target
EOF
fi

if [ "$CODEX" = 1 ]; then
  cat > "$(r $UNITS)/libra-codex.service" <<EOF
[Unit]
Description=Libra: codex lore-wiki website (browse/add your world; MediaWiki-style api.php)
After=network-online.target libra.service
Wants=network-online.target

[Service]
Type=simple
$COMMON
ExecStart=$PREFIX/venv/bin/libra skill --yes run codex_serve host=$CODEX_HOST port=$CODEX_PORT
Restart=on-failure
RestartSec=10
$HARDEN

[Install]
WantedBy=multi-user.target
EOF
fi

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

# ---------------------------------------------------------------- desktop integration
# Applications-menu launchers and the "libra" icon, so Libra appears in the desktop menu
# (Raspberry Pi OS Desktop, GNOME, KDE, ...) and opens the web app in a browser.
mkdir -p "$(r /usr/share/applications)"
cat > "$(r /usr/share/applications)/libra.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Libra
GenericName=AI Control Hub
Comment=Open the Libra app: self-hosted model, agent, knowledge base and world engine
Exec=xdg-open http://localhost:$PORT/
Icon=libra
Terminal=false
Categories=Network;Utility;Development;Education;
Keywords=libra;ai;llm;agent;assistant;wiki;worldbuilding;
StartupNotify=false
EOF
if [ "$CODEX" = 1 ]; then
  cat > "$(r /usr/share/applications)/libra-codex.desktop" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Libra Lore Wiki
GenericName=Lore Manager & World Engine
Comment=Open the Libra codex: a local Fandom-style wiki for your worlds, continuities and characters
Exec=xdg-open http://$CODEX_HOST:$CODEX_PORT/
Icon=libra
Terminal=false
Categories=Network;Utility;Education;
Keywords=libra;wiki;codex;lore;worldbuilding;continuity;
StartupNotify=false
EOF
fi
for pair in "favicon-32.png:32x32" "icon-192.png:192x192" "icon-512.png:512x512"; do
  png="${pair%%:*}"; size="${pair##*:}"
  if [ -f "$SRC_DIR/libra/webui/$png" ]; then
    mkdir -p "$(r /usr/share/icons/hicolor/$size/apps)"
    cp "$SRC_DIR/libra/webui/$png" "$(r /usr/share/icons/hicolor/$size/apps)/libra.png"
  fi
done
if [ -f "$SRC_DIR/libra/webui/icon.svg" ]; then
  mkdir -p "$(r /usr/share/icons/hicolor/scalable/apps)"
  cp "$SRC_DIR/libra/webui/icon.svg" "$(r /usr/share/icons/hicolor/scalable/apps)/libra.svg"
fi
if [ -z "$ROOT" ]; then
  command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications 2>/dev/null || true
  command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor 2>/dev/null || true
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
if [ "$DISCORD" = 1 ]; then
  run_systemctl enable libra-discord.service
  if [ -s "$(r $ETC)/discord-token" ]; then run_systemctl restart libra-discord.service; fi
fi
if [ "$CODEX" = 1 ]; then run_systemctl enable --now libra-codex.service; run_systemctl restart libra-codex.service; fi

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
  LIBRA_HOME="$(r $DATA)/home" "$LIBRA" pair "${PAIR_ARGS[@]}" --name "first phone" || true
  if [ -z "$ROOT" ]; then chown -R "$SVC_USER:$SVC_USER" "$DATA/home"; fi
else
  echo "   It listens on this machine only (port $PORT). See deploy/README.md for a reverse proxy or Tailscale."
fi
echo
echo "   Pair another device (new QR code):  sudo libra-admin pair --token-file $ETC/token --name \"Ana's phone\""
echo "   Paired devices / revoke one:        sudo libra-admin devices list  |  sudo libra-admin devices revoke <id>"
echo "   OpenAI-compatible apps (Nexus Agent): base URL = one of the addresses above + /v1, API key = the token"
echo "   Logs: journalctl -u libra -f        Token: $ETC/token"
echo "   Desktop: on a machine with a screen, open \"Libra\" from the applications menu (opens the app)."
echo "   Training: edit $DATA/libra-auto.json (sources, then \"enabled\": true); runs nightly at $TRAIN_AT"
if [ "$PLUGINS" = 1 ]; then
  echo "   Plugins: the suite is installed and trusted in $PLUGIN_DIR (sudo libra-admin skill list to see them)."
  echo "            Build a world:  sudo libra-admin skill --yes run codex_add \"Thor\" type=character fields=\"race=Asgard\""
fi
if [ "$CODEX" = 1 ]; then
  echo "   Codex wiki: running at http://$CODEX_HOST:$CODEX_PORT/ (no login). Local only by default —"
  if [ "$CODEX_HOST" = 127.0.0.1 ]; then
    echo "               reach it with an SSH tunnel:  ssh -L $CODEX_PORT:127.0.0.1:$CODEX_PORT $(id -un)@$(hostname -s 2>/dev/null || hostname)"
    echo "               then open http://127.0.0.1:$CODEX_PORT/ on your computer. Logs: journalctl -u libra-codex -f"
  else
    echo "               WARNING: bound to $CODEX_HOST with NO authentication — anyone who can reach it can read/add"
    echo "               entries. Prefer 127.0.0.1 + an SSH tunnel, or Tailscale. Logs: journalctl -u libra-codex -f"
  fi
fi
if [ "$DISCORD" = 1 ]; then
  if [ -s "$(r $ETC)/discord-token" ]; then
    echo "   Discord: invite link and check:  sudo libra-admin discord check --token-file $ETC/discord-token"
    echo "            let yourself give it goals:  sudo libra-admin discord allow <your Discord user ID>"
  else
    echo "   Discord: no token yet. Re-run with --discord in a terminal to enter it (typing is hidden),"
    echo "            or save it in $ETC/discord-token (owner root, group $SVC_USER, mode 640), then:"
    echo "            sudo systemctl restart libra-discord"
  fi
fi
if [ "$TAILSCALE" = 1 ] && [ -z "$ROOT" ]; then
  echo "   Tailscale: install the Tailscale app on your phone and sign in with the same account."
fi
if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active" && [ "$HOST" != "127.0.0.1" ]; then
  echo "   Firewall (ufw) is on. Allow your home network, for example:"
  echo "     sudo ufw allow from 192.168.0.0/16 to any port $PORT proto tcp"
  [ "$MDNS" = 1 ] && echo "     sudo ufw allow 5353/udp   # lets phones find $(hostname -s 2>/dev/null || hostname).local"
fi
