---
name: deploy
description: Deploy Libra to the user's Debian server (install or upgrade the services), then check that it came up, and roll back if it didn't
context: fork
disable-model-invocation: true
argument-hint: "user@server [version, e.g. v0.15.0; default: latest main]"
---

Deploy Libra to `$ARGUMENTS`. The first word is the SSH target; an optional second word is the version (a tag) to deploy.

This changes a live server, so only you start it. It runs in a forked context so the long logs stay out of the main conversation.

## 0. Can this session reach the server?

Run `ssh -o BatchMode=yes -o ConnectTimeout=10 <target> true`.

If that fails (a cloud session usually can't reach a home server), **stop here** and give the user the exact commands from steps 2–4 to run on the server. Don't pretend the deploy happened.

## 1. Run the test suite

In this checkout, at the version being deployed (`git checkout <version>`, or the latest `main`):

```bash
pip install -e . && python -m unittest discover -s tests && bash .github/smoke.sh
```

Stop if anything fails. For a tag, its release workflow must also be green on GitHub.

## 2. Build the application

There's nothing to compile: the installer builds Libra on the server, into its own virtual environment at `/opt/libra`, from the checkout it's run from. Prepare that checkout:

```bash
ssh <target> 'cd ~/Libraries- 2>/dev/null || git clone https://github.com/Sweitz1/Libraries-.git ~/Libraries-;
  cd ~/Libraries- && git fetch --tags origin && git checkout <version or origin/main> && git log --oneline -1'
```

Before changing anything, record what's running now, so it can be rolled back:

```bash
ssh <target> '/opt/libra/venv/bin/python -c "import libra; print(libra.__version__)";
  systemctl is-active libra; systemctl cat libra | grep ExecStart'
```

## 3. Push to the deployment target

**The installer doesn't remember its options.** Ask the user which were used the first time (`--tailscale`, `--tailscale-only`, `--port`, `--train-at`, `--discord`, `--no-train`, …) and pass the same ones again, or the upgrade changes how the server is reached.

```bash
ssh -t <target> 'cd ~/Libraries- && sudo ./deploy/install-debian.sh <same options>'
```

The installer keeps the token, data (`/var/lib/libra`), models and settings, and restarts the services. Durable jobs that were running are resumed by the server at start-up.

## 4. Check it came up

```bash
ssh <target> 'systemctl is-active libra libra-agent.timer; systemctl is-active libra-auto.timer || true;
  curl -sf http://127.0.0.1:8000/health; journalctl -u libra -n 20 --no-pager'
```

Use the right port if `--port` was given. `/health` must answer `{"status": "ok", …}`, and the journal must show no traceback. If the user has the app open, ask them to reload it: their sign-in still works, because the token didn't change.

## If it didn't come up: roll back

Check out the previous version recorded in step 2, run the installer again with the same options, and check again. Data is never touched by an upgrade or a rollback.

## Report

Say the version before and after, the options used, and the result of each check, or exactly where it stopped and what the user should run.
