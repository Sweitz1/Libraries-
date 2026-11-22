# Libra — capabilities (v0.16.0)

What Libra can do today, area by area. Libra is a from-scratch GPT-style language model and a set of
tools around it, written in Python. The only runtime dependency is **NumPy** (CuPy is optional for an
NVIDIA GPU). It uses **no pretrained weights, no external model and no API key**: the autograd engine,
transformer, tokenizer, search, translation, web scraping, YAML, QR codes, WebSocket and Discord client
are all implemented in this repository.

- **Size:** ~13,200 lines in [`libra/`](../libra), 202 Python test functions in 28 files, plus Android (JVM)
  and Java-client tests.
- **Runs in ~50 MB of RAM** for everything except training. On a 4-core CPU, an answer takes 0.1–0.2 s.
- **Courses:** [INSTALL.md](../INSTALL.md) (set up the servers and apps) and [LEARN.md](../LEARN.md) (learn
  the code). A `/tutor` skill teaches you to make a change yourself.

## The model, from scratch

| Piece | What it does |
|---|---|
| Autograd engine | Tensors, broadcasting, matmul, fused softmax, layer norm, GELU, cross-entropy, embeddings. |
| GPT transformer | Causal multi-head attention, pre-norm blocks, tied embeddings, a KV cache (~25× faster generation on CPU, identical output). |
| Tokenizer | Byte-level BPE with chat special tokens. |
| Training | AdamW, gradient clipping, warmup + cosine schedule, honest validation, atomic versioned checkpoints, resume, time limit, progress callback. Gradients are per-thread; web-triggered training runs in its own process. |
| Backend | CPU or NVIDIA GPU (CuPy). |
| Benchmarks (`libra eval`) | Planner tool choice; held-out loss and perplexity; behaviour by multiple-choice likelihood; optional generation checks; grounded-answer scoring on your files (`libra eval kb`: right / wrong / honest refusals); a history with deltas. Training can be gated so the behaviour score can't drop. |

## Knowledge, language and translation

- **`libra data`** builds de-duplicated datasets from text, Markdown, HTML, chat exports (OpenAI, ChatGPT,
  ShareGPT, Alpaca), CSV Q/A, URLs, Project Gutenberg and site crawls, and suggests a model size. Downloads
  go through the private-address guard.
- **Knowledge base:** your files become grounded, cited answers (BM25 passage search). It quotes the source
  and refuses rather than guessing. Corrections and style rules are remembered.
- **File formats, read from scratch** (no third-party libraries): text, Markdown, HTML, CSV/TSV, JSON/JSONL,
  `.srt`/`.vtt` subtitles (cited by timestamp), and documents — **DOCX, PPTX, XLSX, EPUB, ODT/ODP/ODS and
  RTF** (`libra/documents.py`, via `zipfile` + XML). PDF via the `pdf_text` plugin. Untrusted documents are
  guarded against XML-entity and zip bombs.
- **Translation:** translation memory (Tatoeba, OPUS/Moses, TMX, JSONL, CSV) with an inverted index; exact →
  adapted near match → optional neural model → word-by-word gloss → pivot through English, each result
  reporting its method and confidence. IBM Model 1 alignment. **Language detection** across 28 languages,
  and romanization of Cyrillic and Greek.

## Skills, tools and trust

- **PDF to text** (`plugins/pdf_text.py`): a stdlib-only extractor (zlib for compressed streams) with a system-`pdftotext` fallback, so a PDF's text can go into the knowledge base. Scanned image-only PDFs still need OCR.
- **Media to text, optional plugins** (`plugins/media_text.py`): `image_ocr` (tesseract), `audio_transcribe` (whisper) and `video_transcribe` (ffmpeg + whisper). Each runs only if its tool is installed and says what to install otherwise, keeping Libra's core free of outside models.
- **Lorebook / worldbuilding extraction** (`plugins/lorebook.py`): the `worldbook` skill turns a fan-wiki or fan-site page into a structured card — title, summary and infobox *spec fields* (class, affiliation, armament …) plus categories — rather than a raw article dump. Fandom/MediaWiki pages are read through the site's MediaWiki API (first `{{Infobox …}}` template → clean key/value, lead paragraph → summary); other sites fall back to scraping a `portable-infobox`/`table.infobox` or definition list. Output is marked outside content and fetched through the guarded fetcher.
- **Search and world building** (`plugins/lorebook.py`): `worldbook_search` full-text searches a fan wiki and pulls the matching pages as cards; `world_build` takes seeds across several wikis (e.g. `https://stargate.fandom.com=Asgard|Daedalus ; https://masseffect.fandom.com=Reapers|Normandy`), searches each, pulls the pages automatically, and writes one combined world JSON (each entry tagged with its source wiki) you can `libra kb add` — so a multi-fandom "world" is built without hand-picking page URLs.
- **Fanfiction.net metadata, codex specs and progression** (`plugins/fanfiction.py`): `fic_meta` reads a story's fandom(s) and metadata; `fic_community` lists/searches the stories in a `/community/` page; `fic_text` returns a chapter's prose; `fic_spec` builds a **spec sheet for a fan-made ship/craft** from the fic's own codex/tech-readout block, optionally glossing the canon tech it references against fandom wikis; and `fic_progression` tracks a **character's evolution** — the new abilities, skills, spells, feats and levels they gain — from LitRPG `[Skill]`/`[Level Up]` notifications and "learned/gained" prose across a chapter or range (for crossovers like SG-1 × Neverwinter Nights, or Wandering-Inn-style `[Skill]` progression). Canon entities still come from `worldbook`; a bot-check page is reported, not mis-parsed. (Shared MediaWiki reader: `libra/wiki.py`.)
- **Lore manager & world engine / codex** (`libra/codex.py`, `libra/codexsite.py`, `plugins/codex.py`): a local world store under `~/.libra/codex` — JSON per entry plus images — organised into named **continuities** (a canon plus **AUs**, alternate universes), so the same subject can exist in several continuities, kept apart. Entries have a **type** (character / location / faction / species / item / tech / event / article) and an optional **date** for timelines. Skills: `codex_add` (continuity, type, fields, body, categories, image, date, links; flags contradictions before changing established lore), `codex_search`, `codex_get`, `codex_card` (render, image embedded in SVG), `codex_continuity` (list/add continuities), `codex_timeline`, `codex_link` (cross-world links), `codex_import`, `codex_export` (Markdown for `libra kb add`). `codex_serve` runs a browsable local website (continuity tabs, entries grouped by type, image upload) whose MediaWiki-compatible `api.php` lets `worldbook`/`world_build` read your canon like a Fandom wiki, and **`codex_build`** generates a static, text-only wiki site (navigated by continuity and type, working cross-world links) — the lorebook is the single source of truth, so the site never drifts. All local; nothing leaves the machine.
- **Image rendering** (`libra/raster.py`): a from-scratch raster image with a built-in bitmap font and a **PNG encoder** (zlib + CRC32), so Libra writes PNGs with no image library and no external tool.
- **Card rendering** (`libra/card.py`, `plugins/cards.py`): `card` renders a Fandom-style **spec card or character card** from a `worldbook`/`fic_spec`/`fic_progression` record or a wiki URL — header, label/value rows, categories/source and an artwork slot. A `.png` is rendered natively via `libra/raster.py`; a `.svg` is vector and can embed real art with `--image`. Libra has no image model, so it does not draw the subject; the slot is a labelled placeholder.
- **12 built-in skills:** `calc`, `scrape`, `crawl`, `links`, `summarize`, `translate`, `detect_language`,
  `romanize`, `remember`, `recall`, `forget`, `time`.
- **Plugins:** `.py` files using `@skill`. A plugin's own risk labels aren't trusted until you accept the
  exact file (`libra trust accept-plugin`); until then its "read" skills ask before running and their output
  is treated as outside content.
- **Skill catalog:** SQLite with a BM25 index and lazy loading, tested at 1,000,000 skills.
- **MCP client:** a server's tools become `mcp.<server>.<tool>` skills.
- **OpenAPI 3 / Swagger 2 importer:** one skill per endpoint, auth from environment variables only.
- **Discord skills** (with a bot token): read a channel (outside content), post and react (changes, checked
  against Discord and reconciled after a crash).
- **Trust policy:** allow / ask / deny per skill, source or risk level; any applicable deny wins; an audit
  log, redacted at any depth.
- **Web:** an HTML parser and CSS selector engine, structured scraping (text, tables, links, images, meta,
  JSON-LD), and a polite same-site crawler (robots.txt, delays, duplicates). Fetching refuses private and
  local addresses (pinned at connect time, every redirect re-checked) and ignores `HTTP(S)_PROXY` unless a
  trusted proxy is declared.

## The agent: plans, goals and durable jobs

- **Planner:** turns plain-language goals into steps run with your skills, with step/item/time limits and
  schedules (`hourly`, `6h`, `daily@07:00`, `weekdays@08:30`).
- **Durable jobs** (`libra jobs`, `/v1/jobs`): plans with explicit dependencies, saved atomically before and
  after every step; resumed after a crash without redoing finished work; approvals bound to the exact
  operation (a SHA-256 of the job, step, skill, risk and arguments) and used once.
- **Checks each change by its effect**, not its reply: a change skill can declare a read-only `verify` and a
  `reconcile`; a job is `done` only when every effect check passes, otherwise `needs_review`; after a crash
  Libra asks whether a change already happened before re-running.
- **A hung call can't hang a job:** each call has a time limit (`max_call_seconds`, 300 s); a hung read fails,
  a hung change waits for a person, and cancel reaches a running call.
- **Per-goal permissions and outside content:** a saved goal is limited to the skills its plan used; output
  from the web, APIs and memory is marked outside content, and a change that would use it waits for a person —
  in jobs, plain runs and autonomous mode.
- **Autonomous mode** (rule-based, AutoGPT-style): recalls memories, repairs a weak plan with read-only
  skills, retries, tries alternatives, checks the result against the goal and adjusts, learns the plan that
  worked, and explains each decision. Saved and app goals run as durable jobs.
- **Memory:** persistent facts, preferences and goal outcomes; BM25 recall with recency and usage bonuses,
  and near-duplicate merging.
- **Unattended training (`libra auto`):** collects from crawls, sitemaps, RSS/Atom feeds, pages, Gutenberg
  and folders; discovers new sources weekly; filters boilerplate and masks e-mails and phone numbers; trains
  a copy within a time limit, gates it on held-out text and keeps it only if it isn't worse (rollback
  available); reports progress and sends notifications.

## Servers, apps and clients

- **HTTP server** (OpenAI-compatible, standard library only): chat, skills, translation, knowledge base,
  jobs, training. Bounded concurrency, request deadlines, body/connection/rate limits, security headers, no
  CORS unless an origin is listed.
- **Authentication:** per-device, revocable, hashed bearer tokens; pairing links never carry the main token;
  lockout after repeated wrong tokens. The **web app signs in with an HttpOnly, SameSite=Strict session
  cookie** (CSRF-checked) instead of keeping a token in page storage.
- **Phone/web app** served at `/`: chat, translation, skills, the Agent tab (jobs and approvals), training,
  pairing. Installable to the home screen.
- **Android app:** the web app in a native shell, with scan-to-pair (token encrypted by the Android
  Keystore) and notifications for jobs waiting, finished goals and training decisions.
- **Java client** (`ai.libra:libra-client`, no dependencies, Java 11+ and Android).
- **Discord bot:** answers @mentions and DMs; an allow list for goals and memory; changes refused unless
  allowed; connects out to Discord's gateway with a from-scratch WebSocket client.
- **Debian service:** `deploy/install-debian.sh` sets up the server, nightly training and an agent timer as
  systemd units, with Tailscale, mDNS and an optional Discord bot. Per-device QR pairing.
- **QR encoder** written from scratch (byte mode, versions 1–10, levels L/M/Q/H), verified against OpenCV.

## Security posture (what to scrutinise)

Fixed and tested: DNS-rebinding SSRF, proxy-safe fetching, per-device tokens, cookie sessions, the
outside-content gate, operation-bound approvals, effect checks and reconcile, durable recovery (crash, full
disk, damaged job, hung call), plugin acceptance. Still open and written down in
[docs/REVIEW.md](REVIEW.md) §13: isolating plugins/MCP servers in a restricted process, and a real
deployment (GPU, Debian + systemd, Tailscale, Discord, a phone) that has not been exercised end to end.

For how it's all verified against one commit, see [docs/VERIFY.md](VERIFY.md); for where to dig,
[docs/INVESTIGATE.md](INVESTIGATE.md).
