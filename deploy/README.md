# Running Libra on Debian and using it from your phone

## 1. Install (about two minutes)

On the Debian machine (Debian 11, 12 or 13, Ubuntu, or Raspberry Pi OS; Python 3.9+), from a copy of this repository:

```bash
git clone https://github.com/Sweitz1/Libraries-.git libra && cd libra
sudo ./deploy/install-debian.sh --tailscale     # or without --tailscale for home Wi-Fi only
```

At the end it prints a **QR code. Point your phone's camera at it** and Libra opens already signed in. To keep it on the home screen, open the browser menu and choose *Add to Home Screen* (*Install app* on Android). It gets its own icon.

To show the QR code again later: `sudo libra-admin pair --token-file /etc/libra/token`. In the app, the **Pair** button shows one for adding your other devices.

| Option | What it does |
|---|---|
| *(none)* | Libra on your home Wi-Fi at `http://<machine>.local:8000`, or the machine's IP address |
| `--tailscale` | Also reachable from anywhere through Tailscale, with HTTPS at `https://libra.<your-tailnet>.ts.net` |
| `--tailscale-only` | Only through Tailscale; nothing is open on your home network |
| `--local-only` | Only this machine (for your own reverse proxy, see section 4) |
| `--port 8080`, `--train-at 03:00`, `--ts-hostname NAME` | Change the port, the nightly training time, or the name on Tailscale |
| `--discord` | Also run Libra as a Discord bot (see *Discord* below) |
| `--no-train`, `--no-agent`, `--no-mdns` | Skip the training timer, the agent timer, or the `.local` name |
| `--uninstall` | Remove the services; your data stays |

Run the installer again after `git pull` to upgrade. Your token, settings and data are kept.

What it sets up, all **starting automatically at boot**:

| | |
|---|---|
| `libra.service` | The app and API. It starts after the network (and Tailscale) and restarts if it stops. It runs as an unprivileged `libra` user with a read-only system |
| `libra-auto.timer` | Nightly training. **Off until you choose sources** (section 3). A failed run is retried up to 3 times |
| `libra-agent.timer` | Runs your scheduled agent goals (checked every 5 minutes) |
| avahi | Announces `<machine>.local` on your Wi-Fi, so phones can find it by name |
| `/var/lib/libra` | Your data: workspace, models, datasets, translation memory, goals, settings |
| `/etc/libra/token` | The access token. Scanning the QR code fills it in, and OpenAI-compatible apps use it as the API key |
| `libra-admin` | Runs any `libra` command as the service, e.g. `sudo libra-admin translate stats` |

## 2. Tailscale: your phone, anywhere

Tailscale is a free private network between your own devices. Nothing is opened on your router, and only devices signed in to your account can reach Libra.

1. Run the installer with `--tailscale`. If this machine isn't signed in yet, it prints a link: open it on any device, sign in, and approve the machine. For a machine without a screen, create an auth key in the Tailscale admin console and run `sudo TS_AUTHKEY=tskey-… ./deploy/install-debian.sh --tailscale`. The key is used once and never saved.
2. **Enable HTTPS once** in the Tailscale admin console: *DNS → enable MagicDNS and HTTPS Certificates*. If it wasn't enabled during install, the installer says so; afterwards run `sudo tailscale serve --bg 8000`.
3. On the phone, install the **Tailscale app** and sign in with the same account.
4. Scan the QR code. It uses `https://libra.<your-tailnet>.ts.net`.

Over HTTPS the app installs as a proper home-screen app, and it can show notifications when training or agent runs finish.

Tailscale's commands and admin pages change from time to time; if a step looks different, check [Tailscale's documentation](https://tailscale.com/kb).

## 3. Add your files, data and goals

The service runs as the `libra` user, so put files where it can read them (for example `/var/lib/libra/import`):

```bash
sudo mkdir -p /var/lib/libra/import && sudo cp -r ~/notes /var/lib/libra/import/ && sudo chown -R libra: /var/lib/libra/import
sudo libra-admin kb add /var/lib/libra/import/notes                        # answers from your files
sudo libra-admin translate add /var/lib/libra/import/pairs.tsv --src en --tgt es
sudo libra-admin translate align --src en --tgt es
sudo libra-admin agent add news "scrape https://example.com/news then summarize it" --every daily@07:00
```

New files are picked up on the next question. Restart the service once after importing translation data (`sudo systemctl restart libra`). Goals can also be created and scheduled in the app's **Agent** tab.

**Training:** edit `/var/lib/libra/libra-auto.json`. Replace the sources with sites, feeds, sitemaps or folders you trust and may train on, then set `"enabled": true`. Libra also discovers each site's own feeds and sitemaps. To get push notifications, set `"notify": {"url": "https://ntfy.sh/<long-random-topic>"}` and subscribe to that topic in the ntfy app. Check what a run would collect with:

```bash
sudo libra-admin auto run /var/lib/libra/libra-auto.json --dry-run
```

The app's **Training** tab shows live progress, the latest report, and *Run now* and *Dry run* buttons. When a run produces a better model, the app switches to it automatically.

## Discord

1. Create a bot at <https://discord.com/developers/applications>: *New Application → Bot → Reset Token*, and copy the token.
2. `sudo ./deploy/install-debian.sh --discord`. It asks for the token (typing is hidden) and stores it in `/etc/libra/discord-token`, readable only by root and the `libra` user.
3. `sudo libra-admin discord check --token-file /etc/libra/discord-token` prints the **invite link**. Open it to add the bot to your server.
4. In Discord, turn on *Settings → Advanced → Developer Mode*, right-click your name, *Copy User ID*, then run `sudo libra-admin discord allow <that ID>`. The bot picks this up without a restart.
5. Mention the bot or DM it: `@Libra help`.

Logs: `journalctl -u libra-discord -f`. The bot only reads messages addressed to it, never pings anyone, and refuses skills that change something unless you've allowed them with `libra trust`.

## 4. Other ways to reach it from outside

Don't forward port 8000 on your router: that puts a plain-HTTP server on the internet. Use Tailscale (section 2), or a reverse proxy with HTTPS on your own domain: `deploy/Caddyfile` is a ready example. Install with `--local-only` so only the proxy can reach Libra, then follow the steps at the top of that file. Either way, the token is still required.

## 5. Other apps (Nexus Agent and OpenAI-compatible clients)

- **Base URL:** `https://libra.<your-tailnet>.ts.net/v1` or `http://<machine>.local:8000/v1`
- **API key:** the token from `/etc/libra/token`
- **Model:** `libra`

## Keeping it running

```bash
systemctl status libra                         # is it up?
journalctl -u libra -f                         # live log
journalctl -u libra-auto -u libra-agent        # training and agent runs
journalctl -u libra-discord -f                 # the Discord bot
systemctl list-timers 'libra-*'                # when they run next
sudo systemctl restart libra                   # after changing /etc/libra/libra.env
```

- **Firewall:** if `ufw` is on, allow only your home network: `sudo ufw allow from 192.168.0.0/16 to any port 8000 proto tcp`, plus `sudo ufw allow 5353/udp` for the `.local` name. Tailscale needs no firewall changes.
- **Phones and `.local` names:** iPhones resolve `.local` names. Many Android phones do too (Android 12 and later), but not all; if yours doesn't, use the IP address link or Tailscale.
- **New token:** `sudo sh -c 'head -c 18 /dev/urandom | base64 | tr "+/" "-_" > /etc/libra/token' && sudo systemctl restart libra`. Every device then has to scan a new QR code.
- **Backups:** back up `/var/lib/libra` and `/etc/libra/token`.
- **Wrong tokens:** 10 wrong tries from one address lock that address out for 10 minutes.
