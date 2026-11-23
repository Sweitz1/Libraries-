# Install Libra: apps and servers

A hands-on course for setting Libra up, from a first run on your own computer to a server that starts at
boot and phones that sign in with a scan. Eight lessons, each with steps you run, a check that it worked,
and a short quiz. Allow about an hour, plus waiting for downloads.

To learn how the code works instead, take [LEARN.md](LEARN.md). For every option in detail, see
[deploy/README.md](deploy/README.md) (the server) and [docs/MOBILE.md](docs/MOBILE.md) (the phone).

`tests/test_learn.py` checks this file as well: every link must resolve, and every `libra` command it
shows must still be a real command.

| # | Lesson | You end up with |
|---|---|---|
| 1 | [The pieces](#1-the-pieces) | a picture of what runs where |
| 2 | [Install on your computer](#2-install-on-your-computer) | the `libra` command |
| 3 | [Run the server and sign in](#3-run-the-server-and-sign-in) | the app in your browser |
| 4 | [Rehearse, then install the Debian service](#4-rehearse-then-install-the-debian-service) | Libra starting at boot |
| 5 | [Reach it from your phone](#5-reach-it-from-your-phone) | home Wi-Fi, or anywhere with Tailscale |
| 6 | [Install the apps and pair them](#6-install-the-apps-and-pair-them) | the home-screen app and the Android app |
| 7 | [Other clients: OpenAI apps, Java and Discord](#7-other-clients-openai-apps-java-and-discord) | Libra inside other tools |
| 8 | [Keep it running](#8-keep-it-running) | logs, upgrades, backups, lost phones |

---

## 1. The pieces

Libra is **one server** and several **clients** that talk to it over HTTP. Nothing goes through anyone
else's service, and no outside AI model is used.

| Piece | Runs on | What it is |
|---|---|---|
| `libra serve` | your computer or a Debian machine | the API and the web app, on port 8000 |
| Debian service | a Debian, Ubuntu or Raspberry Pi OS machine | the same server started at boot, plus nightly training and an agent timer |
| Web app | any phone or computer browser | served by the server at `/`; can be added to the home screen |
| Android app | Android 8.0 or newer | the web app in a native shell, with notifications |
| Java client | your Java 11+ or Android code | a library: `ai.libra:libra-client` |
| Discord bot | the server | Libra answering in your Discord server |

**Two kinds of token.** The server's **main token** is for you and your scripts. Each phone or app gets
its **own device token** when you pair it, so a lost phone can be cut off on its own. In a browser, the
app swaps a token for a session cookie that page scripts can't read.

**What you need:** Python 3.9 or newer and `git`. For lesson 4, a Debian-family machine where you can
use `sudo`. For lesson 5 away from home, a free Tailscale account.

**Quiz**

1. You lose your phone. Which token do you revoke, and does your laptop stop working?
2. Where does the web app come from: an app store, or the server?

<details><summary>Answers</summary>

1. Only that phone's device token. Every other device, and the main token, keep working.
2. The server serves it at `/`. Updating the server updates the app on every phone, with nothing to reinstall.

</details>

---

## 2. Install on your computer

```bash
git clone https://github.com/Sweitz1/Libraries-.git libra && cd libra
python3 -m venv .venv && . .venv/bin/activate
pip install -e .
```

The only thing it downloads is NumPy. Each [release](https://github.com/Sweitz1/Libraries-/releases) also
has a ready-made wheel (`pip install libra_lm-*.whl`) and a `SHA256SUMS` file to check it with
(`sha256sum -c --ignore-missing SHA256SUMS`).

**Prefer a Debian package?** On a Raspberry Pi or Debian/Ubuntu box you can build and install a native
`.deb` instead of the shell installer in lesson 4 — it sets up the service, plugins and lore-wiki the same
way: `bash packaging/build-deb.sh` then `sudo apt install ./dist/libra_*_all.deb`. Or add the **APT
repository** (built from each release) once and install/upgrade by name with `sudo apt install libra`. See
[packaging/README.md](packaging/README.md).

**Check it worked**

```bash
libra --help
libra skill list
```

`libra skill list` prints the built-in skills: calculations, translation, scraping, memory and more.

To practise without touching your real settings, point Libra at a scratch folder first. Everything it
saves goes there instead of `~/.libra`:

```bash
export LIBRA_HOME="$(mktemp -d)"
```

**Quiz**

1. Which folder holds Libra's settings and data, and how do you move it?
2. You downloaded a wheel from a release. How do you check it's the file that was published?

<details><summary>Answers</summary>

1. `~/.libra`, or whatever `LIBRA_HOME` points at.
2. Run `sha256sum -c --ignore-missing SHA256SUMS` in the folder with both files.

</details>

---

## 3. Run the server and sign in

**Just for you** (this computer only, no token):

```bash
libra serve
```

Open <http://127.0.0.1:8000>.

**For other devices on your Wi-Fi**, listen on the network. Libra then creates a token and refuses
requests without it:

```bash
libra serve --host 0.0.0.0
```

It prints something like:

```text
created an access token in ~/.libra/server-token
libra: serving 'libra' on 0.0.0.0:8000
  app:  http://192.168.1.20:8000/#token=…
  API:  http://192.168.1.20:8000/v1 (API key: the token)
```

Open the `app:` link. The token after `#` signs you in once and is then removed from the address bar.
You stay signed in through a cookie that page scripts can't read. **Sign out** ends that session on
the server too.

There's no trained model yet, so chat answers with skills (`what is (2+3)*4`), your files and
translation. Training a model is covered in the README.

**Check it worked** (in a second terminal):

```bash
curl http://127.0.0.1:8000/health                       # {"status": "ok", ...}: no token needed
curl -i http://127.0.0.1:8000/v1/info                   # 401: everything else needs the token
curl -H "Authorization: Bearer $(cat ~/.libra/server-token)" http://127.0.0.1:8000/v1/info
```

**Quiz**

1. You ran `libra serve` without `--host`, and your phone can't connect. Why?
2. Which address answers without a token, and why does that one?

<details><summary>Answers</summary>

1. By default it listens only on `127.0.0.1`, this computer. Use `--host 0.0.0.0`, which also turns the token on.
2. `/health` (and the app's own files), so that monitors can check the server is up without holding a secret.

</details>

---

## 4. Rehearse, then install the Debian service

On a Debian-family machine, one script installs Libra as a service. **Rehearse first.** `--root`
installs everything into a scratch folder, without `sudo`, without users and without starting
anything, so you can read what it would create:

```bash
bash deploy/install-debian.sh --root /tmp/libra-rehearsal
find /tmp/libra-rehearsal -path '*/src' -prune -o -path '*/venv' -prune -o -type f -print
```

You'll see the services (`etc/systemd/system/libra*.service` and `.timer`), the token
(`etc/libra/token`), the `libra-admin` helper and the training config. Then install for real:

```bash
sudo ./deploy/install-debian.sh                 # home Wi-Fi only
sudo ./deploy/install-debian.sh --tailscale     # also reachable from anywhere (lesson 5)
sudo ./deploy/install-debian.sh --full           # the whole suite: all plugins + the codex lore-wiki site
```

At the end it prints a **QR code** and the links. It sets up, all starting at boot:

| | |
|---|---|
| `libra.service` | the app and API, as an unprivileged `libra` user with a read-only system |
| `libra-agent.timer` | runs your scheduled goals every 5 minutes |
| `libra-auto.timer` | nightly training, **off until you choose sources** in `/var/lib/libra/libra-auto.json` |
| plugin suite | worldbuilding/lorebook, fanfiction, cards, codex and document ingest, installed to `/var/lib/libra/home/plugins` and trusted for the service (unless `--no-plugins`) |
| `libra-codex.service` | with `--full` or `--codex`: the codex lore-wiki website, bound to `127.0.0.1:8800` (no login) |
| avahi | announces `<machine>.local` on your Wi-Fi |
| `/etc/libra/token` | the main token |
| `libra-admin` | runs any `libra` command as the service, e.g. `sudo libra-admin devices list` |

Useful options: `--port 8080`, `--train-at 03:00`, `--no-train`, `--no-agent`, `--no-mdns`,
`--no-plugins` (skip the plugin suite), `--codex` / `--codex-port N` / `--codex-host 0.0.0.0` (the lore-wiki
site — localhost-only by default, and it has **no authentication**, so reach it over an SSH tunnel or
Tailscale rather than binding it to your network), `--local-only` (for your own HTTPS proxy, see
[deploy/Caddyfile](deploy/Caddyfile)), `--uninstall` (removes the services; your data stays).

The codex wiki, when on, serves the same world the service owns. Reach it from your computer with:

```bash
ssh -L 8800:127.0.0.1:8800 <you>@<machine>.local      # then open http://127.0.0.1:8800/
sudo libra-admin skill --yes run codex_add "Thor" type=character fields="race=Asgard"   # or build it from the CLI
```

**Check it worked**

```bash
systemctl status libra
systemctl list-timers 'libra-*'
curl http://localhost:8000/health
```

**Quiz**

1. Why rehearse with `--root` before running the installer for real?
2. Training is installed but doesn't run. What turns it on?

<details><summary>Answers</summary>

1. You see every file it would create, and nothing on the system changes: no users, no services, no `sudo`.
2. Edit `/var/lib/libra/libra-auto.json`: list sources you trust and may train on, then set `"enabled": true`.

</details>

---

## 5. Reach it from your phone

**On your home Wi-Fi.** Use `http://<machine>.local:8000`, or the IP address the installer printed.
iPhones resolve `.local` names, and so do many Android phones (Android 12 and later), but not all.
Plain `http://` is readable by others on the same Wi-Fi, so use it only at home.

**From anywhere, with Tailscale** (free, private network between your own devices, nothing opened on
your router):

1. Install with `--tailscale`. If the machine isn't signed in, it prints a link: open it and approve the machine.
   On a machine without a screen: `sudo TS_AUTHKEY=tskey-… ./deploy/install-debian.sh --tailscale` (the key is used once, never saved).
2. In the Tailscale admin console, turn on *DNS → MagicDNS* and *HTTPS Certificates*, once.
3. Install the **Tailscale app** on the phone and sign in with the same account.
4. Use `https://libra.<your-tailnet>.ts.net`.

**Never forward port 8000 on your router.** That puts a plain-HTTP server on the internet. Use
Tailscale, or an HTTPS reverse proxy with `--local-only`.

**Check it worked:** on the phone, with Wi-Fi off and Tailscale on, open the `https://…ts.net` address.

**Quiz**

1. Why prefer the Tailscale address even at home?
2. Your Android phone can't open `atlas.local:8000`. What are your two options?

<details><summary>Answers</summary>

1. It's HTTPS, so nobody on the network can read the token, and the same address works everywhere.
2. Use the IP address link, or Tailscale.

</details>

---

## 6. Install the apps and pair them

**Pairing** makes a new device token and shows it as a QR code. Run it on the server (or press
**Pair** in the web app):

```bash
libra pair --name "Ana's phone"          # a link for the phone's browser
libra pair --app --name "Ana's phone"    # a link for the Android app
```

On the Debian service, run them as the service: `sudo libra-admin pair --token-file /etc/libra/token --name "Ana's phone"`.

**Any phone, in the browser** (iPhone included):

1. Point the camera at the `libra pair` code. Libra opens, already signed in.
2. Browser menu → *Add to Home Screen* (*Install app* on Android). It gets its own icon.

**The Android app** adds notifications (a job waiting for approval, a finished goal, a training decision),
even with the app closed:

1. Download `libra.apk` from a [release](https://github.com/Sweitz1/Libraries-/releases), or from
   *Actions → android → the latest green run → libra-android*.
2. Open it on the phone and allow installing from that source once.
3. Scan the `libra pair --app` code. Check the address is your server, then tap **Connect**.
4. Allow notifications when asked.

**See and revoke devices**

```bash
libra devices list
libra devices revoke <id>        # that device, and any device it paired, are signed out
```

**Check it worked:** the phone shows your server's tabs, and `libra devices list` shows the phone
with a recent *last seen*.

**Quiz**

1. Someone photographs your pairing QR code. What do you do?
2. Which app shows a notification when a job needs your approval?

<details><summary>Answers</summary>

1. Treat it like a password: `libra devices list`, then `libra devices revoke` that device.
2. The Android app (it checks about every 15 minutes). In a browser, the web app's own notifications work only while it's open, over HTTPS.

</details>

---

## 7. Other clients: OpenAI apps, Java and Discord

**OpenAI-compatible apps** (Nexus Agent and others):
- **Base URL:** `https://libra.<your-tailnet>.ts.net/v1` or `http://<machine>.local:8000/v1`
- **API key:** a device token for that app
- **Model:** `libra`

```bash
libra devices add "nexus agent"          # prints a token once: paste it as the API key
```

**The Java client** (`ai.libra:libra-client`, no dependencies, Java 11+ and Android) is published to
GitHub Packages. GitHub needs a personal access token with `read:packages` even to download it. The
setup for Maven and Gradle is in [clients/java/README.md](clients/java/README.md). Then:

```java
LibraClient libra = new LibraClient("https://libra.example.ts.net", "dev_…");
libra.chat("what is (2+3)*4");
```

**The Discord bot**:

1. Create a bot at <https://discord.com/developers/applications> (*New Application → Bot → Reset Token*).
2. `sudo ./deploy/install-debian.sh --discord`. It asks for the bot token (typing is hidden).
3. `sudo libra-admin discord check --token-file /etc/libra/discord-token` prints the invite link.
4. Allow yourself: in Discord, turn on Developer Mode, *Copy User ID*, then `sudo libra-admin discord allow <id>`.
5. Mention it: `@Libra help`.

On a **Debian-package / APT** install there's no `--discord` flag — set the token up with `sudo libra-admin discord setup` (hidden prompt; saves it under the service user), then the packaged `libra-discord.service` picks it up: `sudo systemctl start libra-discord` (it also starts on boot once a token exists). Manage it with `systemctl status libra-discord` and `journalctl -u libra-discord -f`.

**Quiz**

1. Why give Nexus Agent its own device token instead of the main token?
2. The Java client gets a 401. What does that mean?

<details><summary>Answers</summary>

1. So it can be revoked on its own, and so the main token never leaves the server.
2. A wrong or revoked token (a 403 is a skill that needs approval).

</details>

---

## 8. Keep it running

```bash
systemctl status libra                      # is it up?
journalctl -u libra -f                      # live log
journalctl -u libra-auto -u libra-agent     # training and agent runs
systemctl list-timers 'libra-*'             # when they run next
```

| Task | How |
|---|---|
| Upgrade | `git pull`, then run the installer again. Token, settings and data are kept |
| Back up | `/var/lib/libra` and `/etc/libra/token` |
| Lost a phone | revoke that device (lesson 6) |
| Main token leaked | `sudo sh -c 'head -c 18 /dev/urandom \| base64 \| tr "+/" "-_" > /etc/libra/token' && sudo systemctl restart libra`. Browser sessions signed in with the old main token end; device tokens keep working |
| Firewall (`ufw`) | `sudo ufw allow from 192.168.0.0/16 to any port 8000 proto tcp` and `sudo ufw allow 5353/udp`. Tailscale needs nothing |
| Locked out | 10 wrong tokens from one address lock it out for 10 minutes |
| Remove it | `sudo ./deploy/install-debian.sh --uninstall` (data stays in `/var/lib/libra`) |

**Troubleshooting**

| You see | Likely cause |
|---|---|
| The phone can't connect at all | the server listens on `127.0.0.1` only (use `--host 0.0.0.0`, or the Debian service), or `ufw` blocks port 8000 |
| *That token didn't work* | a revoked device, or a mistyped token: pair again |
| `.local` doesn't resolve | the phone doesn't support it: use the IP link or Tailscale |
| *Too many wrong tokens* | wait 10 minutes |
| The Android app won't update | the new APK was signed with a different key: uninstall first, then pair again |
| No notifications | Android's battery saver; open the app once, and allow notifications |

**Quiz**

1. You upgraded and the phones still work. Why didn't they need pairing again?
2. What two things do you need to back up to rebuild the server elsewhere?

<details><summary>Answers</summary>

1. Re-running the installer keeps the token, the device list and the data.
2. `/var/lib/libra` (data and devices) and `/etc/libra/token` (the main token).

</details>

---

## Optional: learn from the web, and your own wiki (plugins)

Libra can build training data straight from the internet and from documents, and keep a personal fan wiki
it pulls from. The core needs nothing extra; the richer sources are **optional plugins** you enable once.

**Train from the web** (no plugin needed):

```bash
libra data --url https://example.org/article --crawl https://example.org/docs --out data/web
libra train data/web --out checkpoints/model        # or let `libra auto` collect → filter → train
```

`libra data` also reads local `.txt .md .html .json .jsonl .csv` and, with the document plugins below,
`.pdf .docx .pptx .xlsx .epub .rtf` and media transcripts. Fetches honour the SSRF guard; add
`--allow-private` only for a LAN/localhost source you trust.

**Enable the plugins** — point Libra at the folder, then accept each file once (its risk labels aren't
trusted until you do):

```bash
cp plugins/*.py ~/.libra/plugins/            # loaded automatically from here (or pass --plugins plugins/)
libra trust accept-plugin ~/.libra/plugins/worldbook.py   # repeat per plugin you use
libra skill list                              # see what's available
```

| Plugin | Gives you |
|---|---|
| `documents` (built in) / `pdf_text` / `media_text` | ingest PDFs, Office/eBook files, and image/audio/video transcripts |
| `lorebook` (`worldbook`, `worldbook_search`, `world_build`) | structured lore/specs from Fandom/MediaWiki fan wikis; search and build a combined world file |
| `fanfiction` (`fic_meta`, `fic_community`, `fic_text`, `fic_spec`, `fic_progression`) | fanfiction.net metadata, a fan-ship spec sheet from a story's codex, and a character's progression |
| `cards` (`card`) | render a Fandom-style spec/character card as PNG (native) or SVG |
| `codex` (`codex_add`, `codex_continuity`, `codex_timeline`, `codex_link`, `codex_search`, `codex_card`, `codex_import`, `codex_export`, `codex_serve`, `codex_build`) | a **lore manager & world engine** under `~/.libra/codex` — named continuities (a canon plus AUs), typed entries (characters/locations/factions/…), timelines and cross-world links, contradiction flagging before established lore changes, a local browsable site (`codex_serve`) and a static wiki generated from it (`codex_build`) |

`codex_serve` runs a browsable local site (`http://127.0.0.1:8800/`) whose MediaWiki-compatible `api.php`
lets `worldbook`/`world_build` read your own codex like a Fandom wiki. It binds to localhost; don't expose
it on `0.0.0.0` (no auth). Libra renders cards and pages from your data but has **no image model**, so it
doesn't draw artwork — upload your own with `--image`.

## Optional: use Libra from Claude Code (MCP)

Libra can act as an **MCP server**, so Claude Code (or the Claude desktop app) lists and calls Libra's
skills — the lore/world engine (`codex_*`), `worldbook`, the fanfiction and card tools, `calc`,
translation, memory — as native tools. Run this on the machine where you use Claude Code (both `claude`
and `libra` must be on its PATH):

```bash
claude mcp add libra -- libra mcp serve
claude mcp list                               # libra: ✓ Connected
```

The tools then appear in your Claude Code session. Every call still goes through Libra's trust gate:
read-only skills run; a write/danger one (e.g. `codex_add`) is refused until you allow it once —

```bash
libra trust allow codex_add
```

**Debian-package (`.deb`) installs:** the plugin suite is trusted under the *service* user's home, so point
the MCP server at it to expose the world engine (otherwise only the built-in skills show up):

```bash
claude mcp add libra -- env LIBRA_HOME=/var/lib/libra/home libra mcp serve
```

On a source/`pip` install, trust the plugins in your own home instead (`libra trust accept-plugin
~/.libra/plugins/codex.py`, as above), then plain `libra mcp serve` is enough. Only stdout carries the MCP
protocol; logs go to stderr.

## Where next

- **Use it:** the [README](README.md) covers training, goals, the knowledge base, translation and skills.
- **Change it:** [LEARN.md](LEARN.md) walks through the code in eight lessons.
- **Check its security:** [SECURITY.md](SECURITY.md) and [docs/INVESTIGATE.md](docs/INVESTIGATE.md).
