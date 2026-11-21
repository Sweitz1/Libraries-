# Changelog

Notable changes to Libra. Versions follow [semantic versioning](https://semver.org); before 1.0, minor versions may change behaviour.

## [Unreleased]

### Security
- Web fetches ignore `HTTP(S)_PROXY` unless `LIBRA_USE_PROXY=1` marks the proxy as trusted. Through an untrusted proxy, a name only the proxy could resolve (an internal host) got past the private-address check. Even with a trusted proxy, names that don't resolve locally are refused.
- `libra data --url` and `--gutenberg` downloads now use the guarded fetcher (they had no private-address check); `libra data --allow-private` covers them.

- Job approvals are bound to the exact operation (a SHA-256 of the job, step, skill, risk and concrete arguments) and shown with it; a changed operation asks again, and `approve` can name the digest it means (`--digest`, `operation_digest`). Older approvals without a digest ask again.
- **Continuing a job after its time limit approved the next step too**, even a change. Now it only lifts the limit.
- Jobs refuse to run where file locking isn't available instead of running without a lock, and the jobs folder is private to its owner (0700).

### Added
- Change skills can declare a read-only `verify` (did it take effect?) and `reconcile` (after a crash, had it already happened?). A failed or inconclusive check makes the job `needs_review`; a confirmed one after a crash isn't repeated. `libra.skills.idempotency_key()` gives each change call a key that is stable across retries, for services that remove duplicates. `remember` has both checks.

- **The web app signs in with an HttpOnly session cookie** instead of keeping a token in page storage (`POST /v1/session`, `/v1/session/logout`). Changes need the app's CSRF header and origin. Sessions end at sign-out, after 30 days, when the device is revoked, or when the main token changes. A token saved by an older version is moved into the cookie and deleted. Bearer tokens are unchanged for scripts and apps.

### Fixed
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
