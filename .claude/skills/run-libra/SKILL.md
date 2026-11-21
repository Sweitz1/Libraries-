---
description: Launches a Libra server locally with a token and drives its phone/web app in a headless browser to see a change working, including the Agent tab's jobs and approvals. Use when asked to run, start, try or screenshot Libra, or to check a web-app or server change in the real app.
argument-hint: "[what to look at, e.g. the Jobs panel]"
---

## Start a server

Use a throwaway home, so nothing touches real data:

```bash
pip install -e . >/dev/null                      # once, if `libra` isn't on the PATH
export LIBRA_HOME=$(mktemp -d)
(libra serve --host 127.0.0.1 --port 8765 --token dev-token > "$LIBRA_HOME/serve.log" 2>&1 &)
sleep 3 && curl -sf http://127.0.0.1:8765/health
```

- **No model trained?** That's fine: chat still answers with skills (`what is (2+3)*4`), files and translation. To test model chat, train a tiny one first: `libra train data --out "$LIBRA_HOME/m" --steps 60 --vocab 400 --ctx 32 --layers 1 --embd 32 --heads 2`, then pass `--model "$LIBRA_HOME/m"` to `serve`.
- **For a job waiting for approval** (the Jobs panel), start the server from Python with `tests/test_jobs.py`'s `build_registry(state)` and the goal `test_jobs.GOAL`. Its `send_report` skill "asks", so the job stops at step 3.
- **For training status**, pass `--auto-config` with a config from `libra auto init`.

## Drive the app

The app's address is `http://127.0.0.1:8765/#token=dev-token`. The token in the fragment signs it in. Use Playwright from Node (`require('playwright')`, already installed). Emulate `devices['iPhone 13']`, which is the app's target, and collect console errors:

```js
const { chromium, devices } = require('playwright');
const b = await chromium.launch(); const ctx = await b.newContext({ ...devices['iPhone 13'] });
const p = await ctx.newPage(); const errs = []; p.on('pageerror', e => errs.push(String(e)));
await p.goto('http://127.0.0.1:8765/#token=dev-token'); await p.click('button[data-tab=agent]');
// … act, then:
console.log(await p.evaluate(() => document.documentElement.scrollWidth > innerWidth), errs);
```

Tabs are `chat`, `translate`, `skills`, `agent` and `training` (`button[data-tab=…]`), plus the Pair screen (`#pair-btn`). `#tab=agent` in the fragment opens a tab directly. Keep screenshots in the scratchpad, not the repository.

## Finish

Report what was seen: the console errors (there should be none), horizontal overflow (there should be none), and the flow's result. Stop the server with `pkill -f "port 8765"`.
