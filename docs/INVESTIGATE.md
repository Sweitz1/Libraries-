# Investigation: Libra 0.15.0 at commit `205f900`

Where to look for problems. The [verification page](VERIFY.md) says what is proven; this page says
what isn't, and where a careful reviewer should push. Everything refers to **one commit**:
[`205f900df691a3a68ad61d221260f54f6eb55242`](https://github.com/Sweitz1/Libraries-/commit/205f900df691a3a68ad61d221260f54f6eb55242) (2026-11-21): Merge pull request #18 from Sweitz1/ccr-6946e33f-4z60nj.

Report what you find privately: https://github.com/Sweitz1/Libraries-/security/advisories/new

## Attack surface

| Entry point | What an attacker controls | Defences | Start reading |
|---|---|---|---|
| HTTP API (`libra serve`) | requests from the network | bearer token on every path except `/health` and the app's static files; lockout; body, connection and rate limits; generation slots | [libra/server.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/server.py) `_authorized`, `OPEN_PATHS`, `_validate` |
| Device tokens and pairing | a stolen or leaked pairing link | per-device tokens, stored only as SHA-256; revocation cascades; links never carry the main token | [libra/devices.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/devices.py), `server.py` `_pair` and `_revoke` |
| Web fetching (skills, crawler, training) | page contents, redirects, DNS answers | robots.txt; non-public addresses refused; connects to the address it checked | [libra/web.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/web.py) `_blocked_ip`, `_safe_connect` |
| Content from the web, APIs, memory | text that looks like instructions | marked as outside content; a change that uses it waits for a person | [libra/jobs.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/jobs.py), `agent.py` `_outside_content_gate` |
| Skills that change things | the goal or chat message | trust rules (any deny wins; ask means no when nobody can be asked); audit log with redaction | [libra/trust.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/trust.py), `skills.py` `SkillRegistry.run` |
| MCP servers, OpenAPI specs, plugins | tool descriptions and code you install | plugins and MCP servers run with your permissions **by design**; descriptions are data | [libra/mcp.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/mcp.py), [libra/openapi.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/openapi.py) |
| Unattended training | crawled text | filters, masking of e-mails and phones, a held-out and behaviour gate before a model is kept | [libra/auto.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/auto.py) |
| Debian installer (runs as root) | its arguments | fixed paths, a shell-less service user, systemd hardening | [deploy/install-debian.sh](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/deploy/install-debian.sh) |
| Android app | the server's pages | a WebView without a JavaScript bridge; other origins open in the browser; scanned links only fill the form | [MainActivity.java](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/android/app/src/main/java/ai/libra/app/MainActivity.java) |
| Discord bot | messages in the channels it's in | answers only messages addressed to it; an allow list for goals and memory; no mentions in replies | [libra/discord.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/discord.py) |

## Known residual risks

Already written down, so a finding here should show a *concrete* exploit or a cheap fix:

1. **Proxies:** ignored unless `LIBRA_USE_PROXY=1` marks one as trusted; a trusted proxy resolves names again itself, so split-horizon DNS is its responsibility.
2. **MCP over HTTP and Discord's REST API** use their configured URLs directly (trusted configuration, often `localhost`).
3. **The web app's sign-in** is an HttpOnly session cookie: script in the app's origin can't read it, but can use it while the page is open (CSP `script-src 'self'` limits it).
4. **Rate limits and connection caps are per address**: many clients behind one NAT share a budget.
5. **Two trainers writing one checkpoint folder** at once would race; `libra auto` holds a lock file against it.
6. **Planner and result checks are rule-based**, read from the goal's wording, and can be fooled by phrasing.

Source: [docs/REVIEW.md](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/docs/REVIEW.md), sections 9 to 13.

## Hypotheses worth testing

Each is a claim the code makes. Try to break it; the starting point is where the defence lives.

| # | Claim | Try | Start at |
|---|---|---|---|
| H1 | No API path answers without a token, except `/health` and static files | odd paths: trailing `/`, `//`, `%2F`, `?` tricks, `HEAD` and other methods | `server.py` `do_GET`, `do_POST`, `_authorized` |
| H2 | A device token can't act beyond its own device | revoke another device; pair new devices from a revoked one; replay after revocation | `server.py` `_revoke`, [libra/devices.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/devices.py) `verify` |
| H3 | Web fetching never reaches a non-public address | IPv6 forms, `[::ffff:127.0.0.1]`, decimal or octal IPv4, DNS that changes between lookups, redirect chains, `file:`/`gopher:` | `web.py`, [tests/test_ssrf.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/tests/test_ssrf.py) |
| H4 | Outside content can't trigger a change without a person | Discord messages, chat with `libra_auto_skills`, memory recall feeding a write step, `foreach` items from a page | `jobs.py` `run`, `agent.py`, [tests/test_jobs.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/tests/test_jobs.py) `PermissionsAndOutsideContentTest` |
| H5 | Any applicable deny wins | prefix rules, catalog skill names with dots, upper/lower case | `trust.py` `Policy.decide` |
| H6 | Secrets never reach the audit log | nested arguments, URLs with credentials, token-shaped strings in lists | `trust.py` `redact` |
| H7 | No path reaches outside its folder | job ids, `libra kb add`, translation files, the static file table | `jobs.py` `_dir`, [libra/knowledge.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/knowledge.py) |
| H8 | A checkpoint can't run code when loaded | a crafted `model.npz` (pickled objects), a huge `config.json` | [libra/checkpoint.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/checkpoint.py) `load` (NumPy's `allow_pickle` default is off) |
| H9 | One client can't exhaust the server | slow bodies, many connections, long generations, huge messages, pathological regex input to the planner | `server.py` `LibraHTTPServer`, `Engine.acquire`, [libra/agent.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/agent.py) |
| H10 | An interrupted job never repeats a change by itself, unless the skill's read-only `reconcile` says it hadn't happened | `kill -9` during a write step, then resume, then the agent timer; a `reconcile` that raises or returns odd values | `jobs.py` `_recover`, `_reconcile`, `tests/test_jobs.py` |
| H11 | An approval covers one exact operation, once | change the arguments, the `{prev}` output, the skill or the risk after approving; replay an approval; approve a time-limit pause and watch the next change | `jobs.py` `_operation`, `_approved`, `approve` |
| H12 | The web app's session cookie can't be used by another site or outlive its sign-in | a cross-site form or `fetch` with the cookie; no or wrong `Origin`; reuse after sign-out, device revocation or a new main token; a token placed in the cookie | `server.py` `_authorized`, `_same_site`, [libra/sessions.py](https://github.com/Sweitz1/Libraries-/blob/205f900df691a3a68ad61d221260f54f6eb55242/libra/sessions.py) |

## Not verified at all

These were never run for real, so anything here is open ground:
a Debian deployment under systemd, Tailscale, Discord's real servers, a GPU, the Android app on a device,
real MCP servers and public APIs, and live websites. There has been no professional security audit.

## How to report

- **Security problems:** privately, at https://github.com/Sweitz1/Libraries-/security/advisories/new (never as a public issue).
- **Other problems:** an issue, using the bug form.
- Include: the commit (`205f900`), the file and line, a concrete input that shows it, and whether you ran it
  or are reading the code. A failing test is the best report.

## A prompt for an AI investigator

```text
Investigate the Libra repository at commit 205f900df691a3a68ad61d221260f54f6eb55242 only:
  https://github.com/Sweitz1/Libraries-/tree/205f900df691a3a68ad61d221260f54f6eb55242
Read the attack surface, residual risks and hypotheses on this page first:
  https://raw.githubusercontent.com/Sweitz1/Libraries-/main/docs/INVESTIGATE.md
and the responses to earlier reviews (sections 9-12) here:
  https://raw.githubusercontent.com/Sweitz1/Libraries-/205f900df691a3a68ad61d221260f54f6eb55242/docs/REVIEW.md
Try to break each hypothesis H1-H12. For each, say: broken (with the file, line and a concrete input),
holds (what you checked), or not checked. Don't report a listed residual risk unless you show an exploit
or a fix. Say clearly which results come from reading the code and which you ran.
```
