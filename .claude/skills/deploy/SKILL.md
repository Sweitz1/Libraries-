---
name: deploy
description: Deploy Libra to the user's Debian server (install or upgrade the services), check that it came up, and roll back if it didn't
context: fork
disable-model-invocation: true
argument-hint: "user@server [version, e.g. v0.15.0] [installer options]"
compatibility: Requires bash, git, Python 3.9+ and key-based SSH to the server, where sudo must work without a password. Otherwise it stops and prints the commands to run there.
---

Deploy Libra to `$ARGUMENTS` (the SSH target, then optionally the version and the installer's options). This changes a live server, so only you start it. It runs in a forked context so the long logs stay out of the main conversation.

## Available scripts

- **`scripts/deploy.sh`**: the whole deploy, with no prompts. It:
  1. checks the server is reachable over SSH, and reads the running version;
  2. runs the tests here;
  3. checks `sudo` works without a password, before changing anything;
  4. checks out the version in the server's checkout (`~/Libraries-`, cloned if missing), remembering the previous one;
  5. runs `deploy/install-debian.sh` with the given options;
  6. checks `/health` answers `ok`, and rolls back if it doesn't.

  It prints one JSON line per step (`step`, `status`, `detail`) on stdout, with progress on stderr. `--dry-run` checks and shows the plan without changing anything. `--help` lists every option.

  | Exit code | Meaning | What to do |
  |---|---|---|
  | 0 | deployed and healthy | report it |
  | 2 | bad arguments | fix them (the error says what was received) |
  | 3 | can't reach the server | the session can't SSH there (common in cloud sessions): give the user the commands below |
  | 4 | the tests failed | fix the code first; don't deploy |
  | 5 | couldn't check out the version | check that the tag exists on GitHub |
  | 6 | sudo needs a password | nothing was changed; give the user the install command from `detail` |
  | 7 | the installer failed | read `/tmp/libra-deploy-install.log` |
  | 8 | was unhealthy, rolled back, healthy again | report the failure; the old version is running |
  | 9 | unhealthy, and the rollback didn't help | urgent: have the user run `journalctl -u libra -n 50` on the server |

## Workflow

1. **Ask which installer options were used the first time** (`--tailscale`, `--tailscale-only`, `--port`, `--train-at`, `--discord`, `--no-train`, …). The installer doesn't remember them, and leaving one out changes how the server is reached.
2. **Dry run:**
   ```bash
   bash scripts/deploy.sh --target <user@server> --version <tag> --options "<options>" --dry-run
   ```
   Show the user the plan, and continue only if every step is `ok` or `planned`.
3. **Deploy:** the same command without `--dry-run`.
4. Act on the exit code, using the table above.

## When the session can't reach the server (exit 3)

Give the user these commands to run on the server, with the same options:

```bash
cd ~/Libraries- && git fetch --tags && git checkout <tag>
sudo ./deploy/install-debian.sh <options>
curl -s http://127.0.0.1:8000/health      # should answer {"status": "ok", …}
```

To roll back, check out the previous tag and run the installer again. Data, the token and models are kept either way.

## Report

Give the version before and after, the options used, every step's status, and the exit code. Never say it's deployed unless `health` was `ok`.
