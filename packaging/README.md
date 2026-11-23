# Debian package (.deb) for Libra

Libra can be installed as a native Debian package, as an alternative to `deploy/install-debian.sh`. The
package is **`Architecture: all`** (Libra is pure Python — NumPy + the standard library), so one `.deb`
works on a Raspberry Pi, a Debian or an Ubuntu machine, on any CPU.

## Build it

```bash
bash packaging/build-deb.sh            # writes dist/libra_<version>_all.deb
bash packaging/build-deb.sh --out /tmp # or choose the output directory
```

Only `dpkg-deb` (from `dpkg-dev`) is needed — no debhelper, no compiling, no network.

## Install it

```bash
sudo apt install ./dist/libra_<version>_all.deb     # pulls python3-numpy automatically
# or:
sudo dpkg -i ./dist/libra_<version>_all.deb && sudo apt-get -f install
```

That installs, and starts at boot:

- `/usr/bin/libra` and `/usr/bin/libra-admin`
- the code under `/usr/share/libra` (depends on the system `python3` + `python3-numpy`)
- the systemd units `libra.service`, `libra-auto.timer`, `libra-agent.timer`, `libra-codex.service`,
  `libra-discord.service` (dormant until a Discord token is set up)
- the **plugin suite** (worldbuilding/lorebook, fanfiction, cards, codex, document ingest) into
  `/var/lib/libra/home/plugins`, trusted for the service

The `postinst` creates the `libra` service user, a token in `/etc/libra/token`, the data under
`/var/lib/libra`, and enables `libra.service`. Then:

```bash
sudo libra-admin pair --token-file /etc/libra/token --name "my phone"   # pair a device
sudo libra-admin skill list                                             # the suite is trusted
sudo systemctl enable --now libra-codex                                 # the lore-wiki site (127.0.0.1:8800)
ssh -L 8800:127.0.0.1:8800 <you>@<host>                                 # then open http://127.0.0.1:8800/
sudo libra-admin discord setup                                          # Discord bot: paste token (hidden), then:
sudo systemctl start libra-discord                                      # (auto-starts on boot once a token exists)
```

## Remove it

```bash
sudo apt remove libra      # stop and remove the services and files; your data in /var/lib/libra stays
sudo apt purge libra       # also remove /var/lib/libra, /etc/libra and the "libra" user
```

## APT repository (`sudo apt install libra`)

For a one-command install **and upgrades**, publish the `.deb` to an APT repository instead of handing out
files. `packaging/build-apt-repo.sh` turns one or more `.deb`s into a static, hostable repo:

```bash
bash packaging/build-apt-repo.sh --out apt-repo dist/libra_*.deb       # or --pool <dir of .debs>
APT_SIGN_KEY=<gpg-key-id> bash packaging/build-apt-repo.sh --out apt-repo dist/libra_*.deb   # signed
```

It writes `pool/main/<debs>`, `dists/stable/main/binary-all/Packages[.gz]`, a `dists/stable/Release` (plus
`InRelease`/`Release.gpg` and the public key when signed), and an `index.html` with install instructions.
Host that folder anywhere static (GitHub Pages, S3, a web server), then users run:

```bash
# unsigned repo:
echo "deb [trusted=yes arch=all] https://<host>/apt stable main" | sudo tee /etc/apt/sources.list.d/libra.list
# signed repo (recommended):
curl -fsSL https://<host>/apt/libra-archive-keyring.gpg | sudo tee /usr/share/keyrings/libra.gpg >/dev/null
echo "deb [signed-by=/usr/share/keyrings/libra.gpg arch=all] https://<host>/apt stable main" | sudo tee /etc/apt/sources.list.d/libra.list

sudo apt update && sudo apt install libra
# later: sudo apt update && sudo apt upgrade
```

**Hosting on GitHub Pages:** the `apt-repo` workflow (`.github/workflows/apt.yml`) builds the repo from the
`.deb`s attached to every release and deploys it to Pages on each new release. One-time setup: in the repo,
**Settings → Pages → Source = "GitHub Actions"**. For a signed repo, add the secrets `APT_GPG_PRIVATE_KEY`
(an ASCII-armored private key) and `APT_GPG_KEY_ID`; without them it publishes unsigned (install line uses
`[trusted=yes]`). Once Pages is live the repo is at `https://<owner>.github.io/<repo>/` and users install
with the lines above.

## Notes

- **Dependencies:** `python3 (>= 3.9)`, `python3-numpy`. `apt install ./file.deb` resolves them; a bare
  `dpkg -i` needs `apt-get -f install` afterwards to pull them.
- **Releases:** the `release` workflow builds this `.deb` (job `debian`) and attaches it to each GitHub
  release next to the wheel, the APK and the Java client, listed in `SHA256SUMS`. `build-deb.sh` reads the
  version from `pyproject.toml`, so the filename tracks the tag.
- The code in `/usr/share/libra` is run read-only against the system Python; the service user only writes
  under `/var/lib/libra`.
- The lore-wiki site has **no authentication** — keep it on `127.0.0.1` and reach it over SSH/Tailscale.
