# Changelog

Notable changes to Libra. Versions follow [semantic versioning](https://semver.org); before 1.0, minor versions may change behaviour.

## [Unreleased]

### Added
- **Document formats, read with the standard library only** (`libra/documents.py`): DOCX, PPTX, XLSX, EPUB, ODT/ODP/ODS and RTF are extracted to text (Office and ebook files are ZIP+XML; RTF is control-word text), so `libra kb add` and `libra data` digest them like any text file. Untrusted documents are guarded against XML-entity bombs (DOCTYPE/ENTITY refused) and zip bombs (uncompressed-size cap).
- **`plugins/media_text.py`**: optional plugins to get text out of media — `image_ocr` (tesseract), `audio_transcribe` (whisper) and `video_transcribe` (ffmpeg + whisper). Each runs only when its tool is installed and otherwise explains what to install, so Libra's core stays free of outside models.
- **`plugins/lorebook.py`**: a `worldbook` skill that builds a *structured lorebook card* from a fan wiki or fan site, instead of dumping the whole article. For Fandom/Wikia and other MediaWiki pages it asks the site's own MediaWiki API for the page wikitext, reads the first `{{Infobox …}}` template into clean key → value spec fields (links, refs and templates stripped) and takes the lead paragraph as the summary; for other pages it scrapes a `portable-infobox`/`table.infobox` or a definition list from the HTML. It guesses the entry kind (character, ship, species, location …) from the infobox name and categories, marks the output as outside content, and fetches through Libra's guarded fetcher (`libra skill run worldbook https://stargate.fandom.com/wiki/Daedalus`).
- **`worldbook_search` and `world_build` (in `plugins/lorebook.py`)**: search-and-pull, so you don't hand over page URLs. `worldbook_search` full-text searches a fan wiki (MediaWiki `list=search`) and pulls the matching pages as lorebook cards; `world_build` takes seeds like `https://stargate.fandom.com=Asgard|Daedalus ; https://masseffect.fandom.com=Reapers|Normandy`, searches each wiki for each term, pulls the pages, and assembles one combined world file (each entry tagged with its source wiki) ready for `libra kb add world.json`. A wiki it can't reach is reported, not silently dropped.
- **Lore manager & world engine — continuities / AUs** (`libra/codex.py`, `libra/codexsite.py`, `plugins/codex.py`): your world is now organised into named **continuities** — a canon ("Main Canon") plus any number of **AUs** (alternate universes) — each with its own entries, so the *same* subject can live in several continuities, kept separate. Entries carry a navigation **type** (character / location / faction / species / item / tech / event / article) and an optional **date** for timelines. New skills: `codex_continuity` (list/add continuities), `codex_timeline` (a continuity's dated entries in order) and `codex_link` (cross-world links between the same subject across AUs, also auto-detected by matching title). `codex_add` takes `continuity`/`type`/`date`/`links`, still **flags contradictions before changing established lore** within a continuity (refused until `force=true`; new fields are fine), and `codex_search`/`codex_import` are continuity-aware. The site and static build navigate by continuity (tabs) and group entries by type; `codex_build` writes `wiki/<continuity>/<slug>.html` with working cross-world links. Existing flat entries migrate into Main Canon automatically. (Replaces the earlier flat official/fanon/mine tier.)
- **Personal fan wiki** (`libra/codex.py`, `libra/codexsite.py`, `plugins/codex.py`): keep your own local wiki of entries (characters, items, tech, ships, places) under `~/.libra/codex` — one JSON per entry plus an `images/` folder — and have the AI pull from it. `codex_add` saves an entry with infobox fields, a body, categories and an optional image; `codex_search` searches it; `codex_get` shows one; `codex_card` renders an entry as a card (embedding its image in SVG); `codex_import` ingests a `worldbook`/`fic_spec`/`fic_progression`/world-file record as entries; `codex_export` writes Markdown for `libra kb add`, so `libra ask` and chat cite your entries; and **`codex_serve` runs a local website** to browse and add entries in a browser (image upload included). Each entry auto-renders as a **full Fandom-style article** — a right-floated portable infobox (title bar, image, Overview group, zebra spec rows, using Fandom's own `portable-infobox`/`pi-data` classes) beside the description, which supports `== Section ==` headings and light wikitext (`'''bold'''`, `''italic''`, `[[links]]`) and a category bar. It also serves a **MediaWiki-compatible `api.php`** so `worldbook`/`world_build`/`worldbook_search` can read your own codex exactly like a Fandom wiki (`libra --allow-private skill run worldbook http://127.0.0.1:8800/wiki/<Title>`). All local — nothing leaves the machine.
- **`libra/raster.py`**: a from-scratch raster image — alpha-blended rectangles, a built-in 5×7 bitmap font (small-caps), and a **PNG encoder** (zlib scanlines + CRC32 chunks) — so Libra can write a PNG with no image library (no PIL) and no external tool.
- **`libra/card.py` + `plugins/cards.py`**: render a Fandom-style **spec card or character card as an image** from a `worldbook`/`fic_spec`/`fic_progression` record (or straight from a wiki URL) — titled header, label/value rows, categories/source, and an artwork slot. A **`.png` is rendered natively** by `libra.raster` (no `rsvg-convert`/`inkscape`); a `.svg` is vector and can embed real art with `--image`. **Libra has no image model, so it does not draw the ship or person** — the slot is a labelled placeholder. `libra skill run card ragnarok.json --out ragnarok.png`.
- **`fic_progression` (in `plugins/fanfiction.py`)**: track a character's **evolution across a fic** — the new abilities, skills, spells, feats and levels they gain — from LitRPG `[Skill]`/`[Level Up]`/`[Class: …]` notifications and "learned/gained/unlocked" prose that names them, across a chapter or a chapter range. Built for crossovers like SG-1 × Neverwinter Nights (D&D) and Wandering-Inn-style `[Skill]` progression.
- **`libra/wiki.py`**: the shared, stdlib-only MediaWiki reader (search, page parse, and wikitext → lead summary / infobox fields) behind the lorebook and fanfiction plugins, so the wiki machinery isn't duplicated across plugins.
- **`plugins/fanfiction.py`**: skills for fanfiction.net, which has no infobox or API — `fic_meta` reads a story page's fandom(s) and metadata line (rating, language, genre, characters, chapters, words, reviews, favs, follows, status, dates, crossover flag); `fic_community` lists — and, with `query`, searches within — the stories in a `/community/` page; `fic_text` returns a chapter's story prose; and `fic_spec` builds a **spec sheet for a fan-made ship/craft** from the fic's own *codex / tech-readout* block (parsing its `Label: value` lines), optionally cross-referencing the canon tech it names against one or more fandom wikis (e.g. a Stargate × Battlestar Galactica crossover's flagship, whose specs exist only in the story). A bot-check page is reported rather than mis-parsed.
- **`plugins/pdf_text.py`**: a PDF-to-text plugin. It extracts text with the standard library only (inflating compressed content streams with `zlib`), falling back to the system `pdftotext` when installed, so a PDF can be added to the knowledge base (`libra skill run pdf_text report.pdf --out report.txt`). A scanned, image-only PDF has no text to pull out and is reported as needing OCR.

- **Debian package build** (`packaging/`): Libra can be built as a native `.deb` (`bash packaging/build-deb.sh` → `dist/libra_<version>_all.deb`), as an alternative to the shell installer. It's `Architecture: all` (pure Python — NumPy + stdlib), depends on `python3-numpy`, installs the code to `/usr/share/libra` with `/usr/bin/libra`, the systemd units and the trusted plugin suite, and its `postinst` sets up the `libra` service user, token and data — so `sudo apt install ./libra_<version>_all.deb` is a one-command install on a Raspberry Pi or Debian box. Only `dpkg-deb` is needed to build (no debhelper).
- **Full-suite installer** (`deploy/install-debian.sh`): the Debian/Raspberry-Pi installer now installs the whole plugin suite (worldbuilding/lorebook, fanfiction, cards, codex, PDF/Office/media ingest) into the service's plugin folder and trusts each for the service (skip with `--no-plugins`), and `--codex` / `--full` adds a `libra-codex.service` running the codex lore-wiki website (bound to `127.0.0.1:8800`, no auth — reach it over SSH/Tailscale; `--codex-host`/`--codex-port` override). `--uninstall` also removes the codex service.

### Fixed
- **A skill call that hung, such as a plugin that never returns, hung its job, and with it the agent timer.** Each call in a job now has a limit (`max_call_seconds`, 300 by default). A read-only step that hangs fails, so the usual read-only alternatives can try, and it isn't retried. A change that hangs pauses the job for a person, since it may still complete. Cancelling a job now also works while a call is running. The abandoned call can't be stopped (Python can't kill a thread), so it finishes or dies with the process in the background.

## [0.16.0] - 2026-11-21

### Documentation
- **INSTALL.md**: an eight-lesson course for installing Libra's servers and apps (your computer, a rehearsal of the Debian installer, the service, Tailscale, the home-screen and Android apps, pairing, OpenAI-compatible apps, the Java client, Discord, upkeep and troubleshooting). `tests/test_learn.py` keeps its links, commands and installer options real.

### Security
- Web fetches ignore `HTTP(S)_PROXY` unless `LIBRA_USE_PROXY=1` marks the proxy as trusted. Through an untrusted proxy, a name only the proxy could resolve (an internal host) got past the private-address check. Even with a trusted proxy, names that don't resolve locally are refused.
- `libra data --url` and `--gutenberg` downloads now use the guarded fetcher (they had no private-address check); `libra data --allow-private` covers them.

- Job approvals are bound to the exact operation (a SHA-256 of the job, step, skill, risk and concrete arguments) and shown with it; a changed operation asks again, and `approve` can name the digest it means (`--digest`, `operation_digest`). Older approvals without a digest ask again.
- **Continuing a job after its time limit approved the next step too**, even a change. Now it only lifts the limit.
- Jobs refuse to run where file locking isn't available instead of running without a lock, and the jobs folder is private to its owner (0700).

### Added
- Change skills can declare a read-only `verify` (did it take effect?) and `reconcile` (after a crash, had it already happened?). A failed or inconclusive check makes the job `needs_review`; a confirmed one after a crash isn't repeated. `libra.skills.idempotency_key()` gives each change call a key that is stable across retries, for services that remove duplicates. `remember` has both checks.

- **The web app signs in with an HttpOnly session cookie** instead of keeping a token in page storage (`POST /v1/session`, `/v1/session/logout`). Changes need the app's CSRF header and origin. Sessions end at sign-out, after 30 days, when the device is revoked, or when the main token changes. A token saved by an older version is moved into the cookie and deleted. Bearer tokens are unchanged for scripts and apps.

- **A plugin's own risk labels aren't believed until you accept it** (`libra trust accept-plugin FILE`, pinned to the file's SHA-256). Until then, its "read" skills ask before running, and their output counts as outside content. `libra trust plugins` lists accepted files and any that changed; `libra trust reset FILE.py` withdraws one. **If you use plugins with read-only skills, accept them once after upgrading.**

- **`libra eval kb`** measures answers from your files on your own question suite: right (quoting the expected passage), wrong (answered from elsewhere), and refused (questions your files can't answer). It ships with a suite for Libra's own docs.

- **Autonomous goals run as durable jobs**, and those jobs adjust their own plan when the result misses the goal's wording (at most twice, read-only skills within the goal's permissions), then learn the plan that worked. Saved autonomous goals and the app's Autonomous button now survive restarts, pause for approval, and keep to their permissions and pre-approvals. Before, a pre-approved change stopped them.

- **A Claude Code plugin marketplace** (`.claude-plugin/marketplace.json`): the project skills install as one plugin with `/plugin marketplace add Sweitz1/Libraries-` and `/plugin install libra-skills@libra`. `scripts/release.sh` keeps its version in step.
- **A `tutor` skill for Claude Code** (`/tutor <change>` or `/tutor lesson <n>`): it plans the change as small steps that you write yourself, gives hints that never give the answer, and checks each step with Libra's tests. It never edits your code. The idea comes from wanna-learn.

- **Discord skills** for goals and the app, with the bot's token: `discord_history` (a channel's messages, treated as outside content), `discord_post` and `discord_react` (changes that ask first, are checked against Discord afterwards, and aren't repeated after a crash). Discord ids are checked to be numbers before they go into an API path. The invite link now includes *Add Reactions*.

### Fixed
- **A release tag with a capital V (`V0.15.0`) did nothing:** the release workflow only runs for lowercase `v` tags, so no APK, packages or checksums were built, and nothing said why. A capital-V tag now stops at a first check that explains, and names the right tag. The Java client's README shows the version to install, and `scripts/release.sh` keeps it current.
- **The Discord bot ignored `!` commands in servers.** Without the Message Content intent, Discord sends a bot empty text for server messages that don't mention it, but the docs and the bot's help said the prefix worked without it. In servers, the help now shows commands as @mentions, and the bot logs which mode it's in. `libra discord set message_content on` (after enabling the intent in the developer portal) makes `!` commands work there.
- A damaged job file (cut off, or not a job) was hidden from `libra jobs list` and the app, and failed with a raw JSON error; the server answered "n must be a number". It's now listed as `damaged`, with the file's path, and resuming carries on with the other jobs.
- A job could fail to start ("already running") if someone listed the jobs at that moment: listing briefly checks each job's lock. Runners and owner decisions now wait up to 2 s for the lock; a real second runner is still refused. Found by a CI run.
- A sign-in link opened in a tab that already showed the app was ignored (only the `#fragment` changed).
- Job event journals are synced to disk, and a line torn by a crash no longer stops `libra jobs` from reading the history.

### Tests
- Two runners on one job, outside content through several steps, and failed read steps never falling back to a change.

## [0.15.0] - 2026-11-21

First public release.

### Added
- **Android app** (`android/`): the web app in a native shell, with scan-to-pair, the sign-in encrypted by the Android Keystore, and notifications for goals waiting for approval, finished goals and training decisions. See [docs/MOBILE.md](docs/MOBILE.md). Not yet run on a real device.
- **Durable jobs** (`libra jobs`, `/v1/jobs`): goals that survive restarts, pause for approval and end with a checked result.
- **Per-goal permissions**, and outside content (web, MCP/API, memories) can no longer drive a change without a person.
- **Benchmarks** (`libra eval`): planner tool choice, held-out loss, behaviour by multiple choice, and a history. Training can require the behaviour score not to drop.
- **Java client** (`ai.libra:libra-client`, `clients/java/`): pairing links, chat, translation, skills, jobs and training status with no dependencies. Published to GitHub Packages and attached to each release.
- **Per-device tokens** (`libra devices`), revocable one by one; pairing links never carry the main token.
- Publishing: issue and pull-request templates, `templates/`, `scripts/release.sh`, and a release workflow that publishes the Python packages and the APK with checksums.

### Security
- Web fetching connects to the address it checked (closes DNS rebinding); a deny rule always wins; audit logs are redacted at any depth; connection, rate and input limits on the server; no CORS unless an origin is listed.

### Fixed
- Generating a reply no longer switches off gradients for training in the same process; web-triggered training runs in its own process.
- Checkpoints are published atomically, so a reload never sees new weights with an old tokenizer.
- Without a held-out split, training reports training loss instead of calling it validation loss.
- Goals with quoted text containing several sentences are no longer split inside the quote.
- `summarize` returned every sentence when all of them were very short, ignoring the requested count. Found by the Java client's live test.

## [0.11.0] and earlier

Development before the first public release: the model, autograd engine, tokenizer and training; GPU support; dataset builder; skills, plugins, the skill catalog, MCP and OpenAPI; grounded answers from files; translation; unattended web training; the Debian installer with Tailscale; the phone web app; the goal agent, autonomous mode, memory and the Discord bot.
