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
- the systemd units `libra.service`, `libra-auto.timer`, `libra-agent.timer`, `libra-codex.service`
- the **plugin suite** (worldbuilding/lorebook, fanfiction, cards, codex, document ingest) into
  `/var/lib/libra/home/plugins`, trusted for the service

The `postinst` creates the `libra` service user, a token in `/etc/libra/token`, the data under
`/var/lib/libra`, and enables `libra.service`. Then:

```bash
sudo libra-admin pair --token-file /etc/libra/token --name "my phone"   # pair a device
sudo libra-admin skill list                                             # the suite is trusted
sudo systemctl enable --now libra-codex                                 # the lore-wiki site (127.0.0.1:8800)
ssh -L 8800:127.0.0.1:8800 <you>@<host>                                 # then open http://127.0.0.1:8800/
```

## Remove it

```bash
sudo apt remove libra      # stop and remove the services and files; your data in /var/lib/libra stays
sudo apt purge libra       # also remove /var/lib/libra, /etc/libra and the "libra" user
```

## Notes

- **Dependencies:** `python3 (>= 3.9)`, `python3-numpy`. `apt install ./file.deb` resolves them; a bare
  `dpkg -i` needs `apt-get -f install` afterwards to pull them.
- **Build in CI / release:** `packaging/build-deb.sh` reads the version from `pyproject.toml`, so it tracks
  releases. It can be run from a release workflow to attach a `.deb` alongside the wheel.
- The code in `/usr/share/libra` is run read-only against the system Python; the service user only writes
  under `/var/lib/libra`.
- The lore-wiki site has **no authentication** — keep it on `127.0.0.1` and reach it over SSH/Tailscale.
