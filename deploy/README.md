# Running Libra on Debian and using it from your phone

## 1. Install (about two minutes)

On the Debian machine (Debian 11, 12 or 13, or Ubuntu; Python 3.9+), from a copy of this repository:

```bash
git clone https://github.com/Sweitz1/Libraries-.git libra && cd libra
sudo ./deploy/install-debian.sh
```

It prints a link like `http://192.168.1.20:8000/#token=…`. **Open that link on your phone** while it's on the same Wi-Fi. The token is saved on the phone, so next time `http://192.168.1.20:8000` is enough. To keep it on the phone's home screen, open the browser menu and choose *Add to Home Screen* (*Install app* on Android).

What the installer sets up:

| | |
|---|---|
| `libra.service` | The app and API, started at boot and restarted if it stops. Runs as an unprivileged `libra` user with a read-only system |
| `libra-auto.timer` | Nightly training (02:30 by default; `--train-at 03:00` to change). **Off until you choose sources**, see step 3 |
| `/var/lib/libra` | Your data: files workspace, models, datasets, translation memory, settings |
| `/etc/libra/token` | The access token. The app asks for it, and OpenAI-compatible apps use it as the API key |
| `libra-admin` | Runs any `libra` command as the service, e.g. `sudo libra-admin translate stats` |

Options: `--port 8080`, `--local-only` (this machine only, for use behind a reverse proxy), `--no-train`, `--uninstall` (keeps your data). Run the installer again after `git pull` to upgrade; your token and settings are kept.

## 2. Add your files and data

The service runs as the `libra` user, so put files where it can read them (for example `/var/lib/libra/import`):

```bash
sudo mkdir -p /var/lib/libra/import && sudo cp -r ~/notes /var/lib/libra/import/ && sudo chown -R libra: /var/lib/libra/import
sudo libra-admin kb add /var/lib/libra/import/notes                        # answers from your files
sudo libra-admin translate add /var/lib/libra/import/pairs.tsv --src en --tgt es
sudo libra-admin translate align --src en --tgt es
```

The app picks up new files on the next question. For translation data, restart the service once (`sudo systemctl restart libra`).

## 3. Switch on automatic training

Edit `/var/lib/libra/libra-auto.json`: replace the sources with sites, feeds or folders you trust and may train on, and set `"enabled": true`. Then:

```bash
sudo libra-admin auto run /var/lib/libra/libra-auto.json --dry-run   # check what it would collect
systemctl list-timers libra-auto.timer                              # when the next run is
```

The app's **Training** tab shows the latest report and has *Run now* and *Dry run* buttons. When a run produces a better model, the app starts using it automatically, with no restart needed.

## 4. Using it away from home

Don't forward port 8000 on your router: that would put a plain-HTTP server on the internet. Pick one of these instead.

**Tailscale (simplest, private).** Tailscale is a free private network between your own devices, and no ports are opened. Install it on the Debian machine and on your phone, and sign in to the same account on both:

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

Then open `http://<machine-name>:8000` on the phone (the name is shown in the Tailscale app). For HTTPS inside your Tailscale network, use `sudo tailscale serve --bg 8000`, and Tailscale prints an `https://…ts.net` address. Commands change between versions, so check Tailscale's documentation if one doesn't work.

**Your own domain with HTTPS (public).** Use a reverse proxy that handles certificates. `deploy/Caddyfile` is a ready example. Install Libra with `--local-only` so only the proxy can reach it, then follow the steps at the top of that file.

Either way, the token is still required for everything.

## 5. Other apps (Nexus Agent and OpenAI-compatible clients)

- **Base URL:** `http://<machine>:8000/v1` (or your `https://` address)
- **API key:** the token from `/etc/libra/token`
- **Model:** `libra`

## Keeping it running

```bash
systemctl status libra          # is it up?
journalctl -u libra -f          # live log
journalctl -u libra-auto        # training runs
sudo systemctl restart libra    # after changing /etc/libra/libra.env
```

- **Firewall:** if `ufw` is on, allow only your home network: `sudo ufw allow from 192.168.0.0/16 to any port 8000 proto tcp`.
- **New token:** `sudo sh -c 'head -c 18 /dev/urandom | base64 | tr "+/" "-_" > /etc/libra/token' && sudo systemctl restart libra`. Every phone will ask for it again.
- **Backups:** back up `/var/lib/libra` and `/etc/libra/token`.
- **Wrong tokens:** 10 wrong tries from one address lock that address out for 10 minutes.
