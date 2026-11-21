# Libra: apps, servers and capabilities (for review)

This is a reviewer's map of the project. It lists every app, server, background service and capability, with links to the exact code. Code links point at commit [`d62c146`](https://github.com/Sweitz1/Libraries-/commit/d62c14694f395f5ad59317b9769697ba3fdad090), so they stay valid while the branch moves.

- **Repository:** <https://github.com/Sweitz1/Libraries->
- **Branch with all current work:** [`ccr-6946e33f-4z60nj`](https://github.com/Sweitz1/Libraries-/tree/ccr-6946e33f-4z60nj). `main` has everything up to PR [#1](https://github.com/Sweitz1/Libraries-/pull/1). Later work (Tailscale, QR pairing, icons, goal agent, autonomy, memory, Discord) is on the branch and not merged yet.
- **CI:** [Actions](https://github.com/Sweitz1/Libraries-/actions). The latest branch runs pass: tests on Python 3.10 and 3.13, and in Debian 11 (Python 3.9) and Debian 12 (Python 3.11) containers, plus an end-to-end smoke script.
- **Related project:** [Sweitz1/nexus-agent-app-lcbtbc](https://github.com/Sweitz1/nexus-agent-app-lcbtbc), the Nexus Agent Android apps. Libra can serve as one of its models (see [Related project](#related-project-nexus-agent-android)). That repository may be private; reviewers need access to open those links.

## 1. What Libra is

Libra is a small GPT-style language model and a set of tools around it, written from scratch in Python. The only runtime dependency is **NumPy**; CuPy is optional for NVIDIA GPUs. It uses **no pretrained weights, no external model and no API key**. Everything else (autograd, transformer, tokenizer, search, translation, web scraping, YAML, QR codes, WebSocket, the Discord client) is implemented in this repo.

Libra's own model is tiny, so the project avoids depending on it for facts or planning:
- Grounded answers **quote** the user's files, with citations.
- Translation uses translation memory and word alignment.
- The agent plans **with rules and search**, and refuses rather than guessing.

The model is used for free-form chat, and can be trained on the user's data and on translation pairs.

- **Size:** about 11,500 lines across [`libra/`](https://github.com/Sweitz1/Libraries-/tree/d62c14694f395f5ad59317b9769697ba3fdad090/libra) (Python, plus the web app's HTML/JS) and [`deploy/`](https://github.com/Sweitz1/Libraries-/tree/d62c14694f395f5ad59317b9769697ba3fdad090/deploy).
- **Tests:** 104 test functions in 16 files under [`tests/`](https://github.com/Sweitz1/Libraries-/tree/d62c14694f395f5ad59317b9769697ba3fdad090/tests).
- **User documentation:** [`README.md`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/README.md) and [`deploy/README.md`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/deploy/README.md).

## 2. Apps (what a person uses)

| App | What it is | Code |
|---|---|---|
| **Phone/browser app** | Mobile-first web app (installable to the home screen). Tabs: Chat (your files or the model), Translate, Skills, Agent, Training, plus a Pair screen with a QR code. Token login. Light and dark themes. No external files, strict CSP, and server text is always inserted as `textContent` | [`webui/index.html`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/webui/index.html), [`webui/app.js`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/webui/app.js), icons and [manifest](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/webui/manifest.webmanifest) |
| **Command line** (`libra`) | 19 commands: `train generate chat serve skill mcp api trust kb translate auto agent discord memory pair scrape crawl data info` | [`cli.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/cli.py) |
| **Terminal chat** (`libra chat`) | Chat with the model, or grounded chat with `--workspace`; `/commands` for skills, `/add` `/correct` `/rule` for files | [`cli.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/cli.py) (`cmd_chat`) |
| **Discord bot** (`libra discord`) | Answers mentions, DMs and `!` commands: ask, translate, goal, plan, goals, run, remember, recall, forget, skills, status, help. Has an allow list for goals and memory changes | [`discord.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/discord.py) |
| **Python library** | `from libra import GPT, BPETokenizer, generate, load, skill, …` | [`__init__.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/__init__.py) |
| **Debian installer** | One command sets up the services below, plus optional Tailscale, Discord and the `.local` name, and prints a QR code | [`deploy/install-debian.sh`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/deploy/install-debian.sh) |

## 3. Servers and services

### 3.1 Libra HTTP server (`libra serve`)

Standard library only (`ThreadingHTTPServer`): [`server.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/server.py). It is OpenAI-compatible, so Nexus Agent and other clients can use it.

**Auth.** With a token set, every route except the open ones below needs `Authorization: Bearer <token>` (or `X-Libra-Token`); the token doubles as the "API key" in OpenAI clients.
- **When a token is used:** a server bound to a network address creates and saves one automatically (mode 0600). A local-only server needs none, and `--no-auth` turns it off explicitly, with a warning.
- **Lockout:** 10 wrong tokens from one address lock it out for 10 minutes (HTTP 429).
- **Size limit:** bodies over 2 MB get 413. The body is drained up to 16 MB first, so clients see the error instead of a broken pipe.
- **Comparison:** tokens are compared with `hmac.compare_digest`.

| Route | Auth | Purpose |
|---|---|---|
| `GET /`, `/app.js`, `/manifest.webmanifest`, icons | open | The web app shell; it asks for the token |
| `GET /health` | open | Liveness |
| `GET /v1/models`, `POST /v1/chat/completions`, `POST /v1/completions` | token | OpenAI-compatible chat (with SSE streaming). Extras: `libra_mode: "model"`, `libra_auto_skills`. With `--workspace`, chat is grounded in the files |
| `GET /v1/info` | token | What's enabled: model, workspace, catalog size, translation, training |
| `GET /v1/skills[?q=]`, `POST /v1/skills/<name>` | token | List or search skills as OpenAI tool schemas; run one. Returns 403 when the trust policy needs an approval |
| `POST /v1/workspace/ask`, `/correct`, `/rule` | token | Grounded answers from files; save corrections and rules |
| `POST /v1/translate` | token | Translation with method, confidence and notes |
| `GET /v1/auto/status`, `POST /v1/auto/run` | token | Training report, live progress, start a run (background thread) |
| `GET /v1/agent`, `POST /v1/agent/plan`, `/run`, `/auto`, `/goals`, `/remove` | token | Goal agent: plan, run, autonomous run, saved goals |
| `GET /v1/pair?base=` | token | Pairing link and QR code (SVG) for another device |

The server reloads the model when a new checkpoint appears (after nightly training), without a restart.

### 3.2 Background services (installed by `deploy/install-debian.sh`)

| Unit | Kind | What it runs |
|---|---|---|
| `libra.service` | long-running | `libra serve`: the app and API. Starts after the network, Tailscale and avahi; `Restart=on-failure` |
| `libra-auto.service` + `.timer` | nightly (default 02:30) | `libra auto run`: collect, filter, train, gate. Off in its config until sources are chosen. `Restart=on-failure` every 20 min, up to 3 times |
| `libra-agent.service` + `.timer` | every 5 min | `libra agent run-due`: scheduled goals |
| `libra-discord.service` | long-running (with `--discord`) | `libra discord run`. Only starts if `/etc/libra/discord-token` exists |
| avahi service file | mDNS | Announces `_http._tcp` and `<hostname>.local` |
| `tailscale serve` | with `--tailscale` | HTTPS at `https://<name>.<tailnet>.ts.net` → `127.0.0.1:<port>` |

All units run as an unprivileged `libra` user with systemd hardening: `ProtectSystem=strict`, `ProtectHome`, `NoNewPrivileges`, `PrivateTmp`, kernel and control-group protections, and `UMask=0077`. The only writable path is `/var/lib/libra`. Tokens live in `/etc/libra/token` and `/etc/libra/discord-token`, owned by root, group `libra`, mode 0640.

Installer options: `--port --host --local-only --tailscale --tailscale-only --ts-hostname --discord --train-at --no-train --no-agent --no-mdns --no-apt --uninstall`, plus `--root DIR` (a test mode used in CI). A Tailscale auth key can be passed once as `TS_AUTHKEY` and is never written to disk. Re-running the installer upgrades in place and keeps the token, config and data.

### 3.3 Outbound clients (Libra connects out; nothing listens)

| Client | Protocol | Code |
|---|---|---|
| Discord gateway + REST | WebSocket (RFC 6455, written here) + HTTPS | [`websocket.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/websocket.py), [`discord.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/discord.py) |
| MCP servers | JSON-RPC over stdio, or Streamable HTTP with SSE replies and `Mcp-Session-Id` | [`mcp.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/mcp.py) |
| Web APIs from OpenAPI/Swagger specs | HTTPS; credentials read from environment variables at call time | [`openapi.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/openapi.py) |
| Web fetcher, scraper, crawler | HTTP(S): robots.txt, rate limits, size cap, private-address guard on every redirect | [`web.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/web.py) |
| Notifications | POST to ntfy, a Discord webhook or any URL | [`notify.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/notify.py) |

## 4. Capabilities, by area

### Model and training
- **Autograd engine:** tensors, broadcasting, matmul, fused softmax, layer norm, GELU, cross-entropy and embedding. [`autograd.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/autograd.py)
- **GPT transformer:** causal multi-head attention, pre-norm blocks, tied embeddings, and a KV cache (about 25× faster generation on CPU, with identical output). [`nn.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/nn.py), [`generate.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/generate.py)
- **Byte-level BPE tokenizer** with chat special tokens. [`tokenizer.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/tokenizer.py), [`chat.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/chat.py)
- **Training:** AdamW, clipping, warmup and cosine schedule, validation, checkpoints, resume, time limit and a progress callback. [`train.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/train.py), [`optim.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/optim.py), [`checkpoint.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/checkpoint.py)
- **CPU / NVIDIA GPU backend** (CuPy). [`backend.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/backend.py)
- **Measured resources** (4-core CPU):
  - everything except training runs in about 35–80 MB of RAM
  - training the small model (0.9M parameters) needs about 0.9 GB of RAM, or 0.26 GB with `--batch 4`, at about 2,500 tokens/s
  - training the medium model (5M parameters) needs about 6.6 GB, or 0.95 GB with `--batch 2`

### Data
- **`libra data`:** builds de-duplicated datasets from text, Markdown, HTML, chat exports (OpenAI, ChatGPT, ShareGPT, Alpaca), CSV Q/A, URLs, Project Gutenberg and site crawls. It strips Gutenberg boilerplate and suggests a model size. [`dataset.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/dataset.py)

### Grounded answers from the user's files (`libra kb`)
- Indexes text, Markdown, HTML, CSV, JSON chats and SRT/VTT transcripts. Media files are covered through a same-name transcript or description.
- Answers quote sentences found with BM25 and cite the **file and line, or media and timestamp**.
- It replies "not found" instead of guessing, and asks a **clarifying question** when files disagree. Follow-ups are resolved statelessly from the conversation.
- Corrections and rules persist, and the data can be exported for training. [`knowledge.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/knowledge.py)

### Translation and language (`libra translate`)
- **Language detection:** writing system, common words, unique letters, word endings and trigram profiles (28 of 28 test languages). Romanization of Cyrillic and Greek. [`lang.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/lang.py)
- **Translation memory** (Tatoeba, OPUS/Moses, TMX, JSONL, CSV) with an inverted index.
- **Methods, in order:** exact match → adapted near match → optional neural model → word-by-word gloss → pivot through English. With no data the answer is "none". Each result reports its method and confidence, plus a gender-agreement warning where relevant.
- **IBM Model 1** alignment, symmetrised in both directions. [`translate.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/translate.py)

### Skills, plugins, catalog, MCP, OpenAPI, trust
- **Built-in skills** (risk level in brackets): `calc [read]`, `crawl [read]`, `detect_language [read]`, `forget [write]`, `links [read]`, `recall [read]`, `remember [write]`, `romanize [read]`, `scrape [read]`, `summarize [read]`, `time [read]`, `translate [read]`.
- **Plugins** are `.py` files using `@skill`, loaded from files, folders or `~/.libra/plugins`. [`skills.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/skills.py), [example plugin](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/plugins/example_tools.py)
- **Skill catalog:** SQLite with an inverted BM25 index and lazy loading. Tested at 1,000,000 skills: 73 s to build, 567 MB, 140–260 ms per search. [`catalog.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/catalog.py)
- **MCP client:** server tools become `mcp.<server>.<tool>` skills. [`mcp.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/mcp.py)
- **OpenAPI 3 / Swagger 2 importer** (JSON, or YAML through a YAML subset reader written here): one skill per endpoint, auth from environment variables only. [`openapi.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/openapi.py), [`yamlish.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/yamlish.py)
- **Trust controls:**
  - Every skill has a risk level: read, write or danger. It comes from MCP annotations, the HTTP method, or verbs in the tool's name.
  - Allow/ask/deny rules can target a skill, a source prefix or a risk level. Deny always wins.
  - Confirmation happens in the terminal. Non-interactive contexts (server, Discord, timers) refuse "ask" skills.
  - Calls are recorded in an audit log, with secrets redacted. [`trust.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/trust.py)

### Web
- **HTML parser and CSS selector engine:** tags, `#id`, `.class`, attribute operators, `:first-child`, `:last-child`, `:nth-child`, descendant and child combinators. [`web.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/web.py)
- **Structured scraping:** text, tables, links, images, meta and JSON-LD.
- **Polite same-site crawler:** robots.txt, delays, duplicate detection.

### Agents (`libra agent`)
- **Planner:**
  - **How a goal is split:** at "then", ";", new lines, or "and" before an action word.
  - **How each part is matched:** fixed phrases (summarize, translate … to X, detect language) first, then the skills' own routers, then catalog search accepted only when it covers at least half of the part's key words.
  - **Arguments:** URLs, quoted text, languages, and numbers named by the word next to them.
  - **Passing results along:** pronouns pass the previous output on, and "each of them" loops over items.
  - **Learned procedures:** reused when a goal's shape matches one that worked before.

  [`agent.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/agent.py)
- **Executor:** runs under the trust policy with step, item and time limits. Saved goals have schedules (`hourly`, `6h`, `daily@07:00`, `weekdays@08:30`, …).
- **Autonomous loop** (AutoGPT-style, rule-based), one goal at a time:
  1. recalls related memories
  2. repairs the plan with read-only fallbacks
  3. retries temporary errors once
  4. tries alternative skills when a step fails
  5. checks the result against the goal's wording (sentence count, quoted words, translation language, non-empty)
  6. adjusts the plan, e.g. adds a summarize step
  7. records thoughts with reasoning and criticism
  8. learns the working plan as a procedure
  9. remembers the outcome

  [`autonomy.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/autonomy.py)
- **Memory:** persistent facts, preferences and goal outcomes. BM25 recall with recency and usage bonuses, and near-duplicate merging. [`memory.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/memory.py)

### Unattended training (`libra auto`)
- **Collects** from crawls, sitemaps (including indexes), RSS/Atom feeds, single pages, Gutenberg books and local folders.
- **Discovers new sources** automatically: sitemaps from robots.txt and `/sitemap.xml`, and feed links, re-checked weekly.
- **Retries** temporary errors only.
- **Filters** boilerplate, short, symbol-heavy, wrong-language, repetitive and duplicate text, and **masks e-mail addresses and phone numbers**.
- **Trains a copy** within a time limit, **gates** it on held-out text, and keeps it only if it isn't worse. Rollback is available.
- **Reports** live progress and a summary, sends notifications, and uses a lock and limits so runs can't overlap or run away. [`auto.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/auto.py)

### Pairing and remote access
- **QR encoder** written from scratch: byte mode, versions 1–10, levels L/M/Q/H. Verified against OpenCV's two decoders for all 640 version/level/mask combinations. [`qr.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/qr.py)
- **Pairing:** chooses the best address (Tailscale HTTPS, then `.local`, then the LAN IP) and builds a link with `#token=` in the URL fragment. The app removes the token from the address bar after reading it. [`pair.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/pair.py)

## 5. Security model (what to scrutinise)

| Boundary | Protection | Where |
|---|---|---|
| Network access to the server | Bearer token (auto-created for network binds), lockout, 2 MB body limit, CSP and security headers; Tailscale or an HTTPS proxy recommended instead of open ports | [`server.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/server.py), [`cli.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/cli.py) `_server_token` |
| SSRF through web skills, crawler, OpenAPI and notifications | Private, loopback, link-local and reserved addresses refused (including every redirect hop) unless `--allow-private` | [`web.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/web.py) `Fetcher._check_url` |
| Skills that change things | Trust policy: ask or deny by risk, source or skill; refused when nobody can be asked; audit log | [`trust.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/trust.py), `SkillRegistry.run` in [`skills.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/skills.py) |
| Plugins and MCP servers | Run arbitrary code with the user's permissions; documented, and only loaded explicitly (or from `~/.libra/plugins`) | [`skills.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/skills.py) `load_plugins`, [`mcp.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/mcp.py) |
| Secrets | API keys only as environment-variable names; the server token in 0600/0640 files; the Discord token entered hidden, never in argv; `TS_AUTHKEY` used once; redaction in audit logs | [`openapi.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/openapi.py), [`install-debian.sh`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/deploy/install-debian.sh) |
| Calculator | AST evaluation of numbers and operators only, with an exponent cap; never `eval` | [`skills.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/skills.py) `_eval` |
| Discord | Reads only messages addressed to it; allow list for goals and memory changes; `allowed_mentions: {parse: []}` on every reply; write skills refused | [`discord.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/discord.py) |
| Web app | No `innerHTML` with server data; CSP `script-src 'self'`; token kept in `localStorage`, taken from the URL fragment once | [`app.js`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/webui/app.js) |
| Installer (runs as root) | Fixed paths; the service user has no shell; systemd hardening; data owned by `libra`; test mode for CI | [`install-debian.sh`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/deploy/install-debian.sh) |
| Crawled training data | robots.txt, rate limits, page and size budgets, e-mail and phone masking; licensing is left to the user (documented) | [`auto.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/libra/auto.py) |

## 6. Testing

| Test file | Tests | Covers |
|---|---|---|
| [`test_autograd.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_autograd.py) | 12 | Numerical gradient checks for every op |
| [`test_model.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_model.py) | 10 | Tokenizer, full-model gradient check, overfitting, checkpoints, server, streaming |
| [`test_fast.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_fast.py) | 7 | KV cache matches the full forward pass, sliding window, backend selection |
| [`test_dataset.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_dataset.py) | 4 | Gutenberg, HTML, chat formats, building a dataset |
| [`test_web.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_web.py) | 11 | Selectors, extraction, robots.txt, private-address guard, crawl, skills, plugins |
| [`test_ecosystem.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_ecosystem.py) | 9 | Catalog, MCP (fake stdio and HTTP servers), OpenAPI (YAML and Swagger 2), trust, server 403 |
| [`test_knowledge.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_knowledge.py) | 11 | Citations (lines and timestamps), not-found, clarifying questions, corrections, rules, grounded server |
| [`test_translate.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_translate.py) | 9 | Detection across 28 languages, memory, alignment, adaptation, pivot, formats, neural pipeline |
| [`test_auto.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_auto.py), [`test_auto_more.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_auto_more.py) | 5 | Full train/gate/rollback cycle, discovery, sitemaps, retries, progress, notifications |
| [`test_remote.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_remote.py) | 6 | Auth, lockout, CSP, size limit, model reload, token rules, installer arguments |
| [`test_agent.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_agent.py) | 6 | Planning, chaining, foreach, trust, limits, schedules, endpoints, pairing |
| [`test_autonomy.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_autonomy.py) | 7 | Memory, check → adjust → learn → reuse, alternatives, retry, blocked, limits |
| [`test_qr.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_qr.py) | 2 | QR structure; independent OpenCV decoding (in CI) |
| [`test_websocket.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_websocket.py) | 3 | Frames, fragmentation, pings, close codes, bad handshakes |
| [`test_discord.py`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/tests/test_discord.py) | 2 | A full bot session against a fake gateway and REST: identify, heartbeat, resume, 429 retry, allow list, memory, goals, fatal close |

The end-to-end CLI checks are in [`.github/smoke.sh`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/.github/smoke.sh), and CI is defined in [`.github/workflows/test.yml`](https://github.com/Sweitz1/Libraries-/blob/d62c14694f395f5ad59317b9769697ba3fdad090/.github/workflows/test.yml). The phone app was also exercised by hand with Playwright (iPhone 13 profile): login, chat, translate, skills, agent, autonomous mode, training progress, pairing (the on-screen QR code decoded with OpenCV), dark mode, no horizontal overflow and no console errors.

**Not verified.** The build environment had no GPU, no systemd, no Tailscale, no Discord, no real phones and no outside network, so these have never run for real:
- the GPU path
- a real Debian install under systemd
- Tailscale
- Discord's real servers
- real MCP servers and public APIs
- live websites
- `.local` resolution on phones
- large real corpora such as Tatoeba or OPUS

## 7. Known limitations and design decisions

- **Planning and autonomy are rule-based.** There is no large language model; this follows the project's rule of using no outside models. They work for goals built from available skills, and refuse rather than guess when a step has no confident match. Weaker matches are only allowed for read-only skills.
- **Search matches words, not meaning.** This applies to grounded answers, translation memory and the skill catalog; paraphrases can come back "not found".
- **Translation quality depends entirely on the user's bilingual data.** Word swaps can't fix grammatical agreement, so results carry a warning instead.
- **Libra's own model is small**, trained from zero on the user's data; it's not a source of world knowledge.
- **Risk levels are sometimes guessed** from tool names when a tool doesn't declare one. The guess errs toward asking (e.g. `add` is treated as a write).
- **`yamlish.py` is a lenient YAML subset reader** (no anchors or tags). It accepted one malformed CI file that strict YAML rejects, so the CI workflow is now validated with PyYAML.
- **One goal at a time** per server and per bot. Training runs use a lock.

## 8. Suggested review focus

1. **Auth and token flow:** `_authorized`, `_server_token`, how pairing links carry the token, and the web app's storage.
2. **SSRF guard:** `Fetcher._check_url`, the redirect handler, and DNS rebinding (not handled; resolution is checked before connecting).
3. **The installer as root:** quoting, idempotency, file modes, and the Tailscale repository setup.
4. **The trust gate:** whether every path that runs a skill goes through `SkillRegistry.run`, including Discord, the server, timers and autonomy.
5. **Untrusted input parsing:** the HTML parser, the YAML reader, sitemap/feed XML (`xml.etree`), TMX, and the WebSocket frame parser (size caps).
6. **Concurrency:** server threads with shared engine state, background agent and training threads, and the Discord heartbeat thread.
7. **Correctness of the from-scratch pieces:** autograd (gradient-checked), BPE, BM25 in SQL, IBM Model 1, the QR encoder, and the WebSocket framing.

## Related project: Nexus Agent (Android)

[Sweitz1/nexus-agent-app-lcbtbc](https://github.com/Sweitz1/nexus-agent-app-lcbtbc) at [`ac0259e`](https://github.com/Sweitz1/nexus-agent-app-lcbtbc/tree/ac0259e239c26b4b507db60e736af66f737d8e65) is a separate repository (Android, Java and JavaScript). It is a multi-model agent app for the RayNeo X3 Pro AR glasses and Android 8+ phones, using providers the user configures (Venice, Anthropic, xAI, OpenAI, Gemini, OpenRouter, Groq, local Ollama/LM Studio and any OpenAI-compatible endpoint, which includes Libra).

| App or server | What it is | Where |
|---|---|---|
| **Nexus Agent** (`com.nexus.agent`, v2.1.0) | The full app: agents in solo/council/chain/translate modes, 233 skills, AutoGPT-style goals, a wake word, and Google, email, music and glasses integrations | [`android/app`](https://github.com/Sweitz1/nexus-agent-app-lcbtbc/tree/ac0259e239c26b4b507db60e736af66f737d8e65/android/app) |
| **Nexus Glasses** (`com.nexus.agent.glasses`, v1.0.0) | A lightweight glasses client for a phone running Nexus. It pairs with a 6-digit code or QR code and finds the phone over mDNS (`_nexus._tcp`) | [`android/glasses`](https://github.com/Sweitz1/nexus-agent-app-lcbtbc/tree/ac0259e239c26b4b507db60e736af66f737d8e65/android/glasses) |
| **Phone server** (port 8787 by default) | A token-protected LAN server inside the app: `/client/…` for the glasses, plus the webhook API `POST /run`, `GET /run`, `POST /hook/<name>`, `/speak`, `/notify`, `GET /agents`, `/config`, `/config/keys`, `/config/import`. It runs only while the app is open | [`WebhookServer.java`](https://github.com/Sweitz1/nexus-agent-app-lcbtbc/blob/ac0259e239c26b4b507db60e736af66f737d8e65/android/app/src/main/java/com/nexus/agent/WebhookServer.java), [`server-phone.js`](https://github.com/Sweitz1/nexus-agent-app-lcbtbc/blob/ac0259e239c26b4b507db60e736af66f737d8e65/android/app/src/main/assets/js/server-phone.js) |
| **APK build** | A GitHub Actions workflow that builds the release, debug and glasses APKs | [`build-apk.yml`](https://github.com/Sweitz1/nexus-agent-app-lcbtbc/blob/ac0259e239c26b4b507db60e736af66f737d8e65/.github/workflows/build-apk.yml) |

**To use Libra from Nexus:** set the base URL to `https://libra.<tailnet>.ts.net/v1` or `http://<machine>.local:8000/v1`, the API key to the Libra token, and the model to `libra`.

This document doesn't review the Nexus code itself; its README describes its features and safety notes: [README](https://github.com/Sweitz1/nexus-agent-app-lcbtbc/blob/ac0259e239c26b4b507db60e736af66f737d8e65/README.md).
