# Changelog

Notable changes to Libra. Versions follow [semantic versioning](https://semver.org); before 1.0, minor versions may change behaviour.

## [Unreleased]

## [0.15.0] - 2026-11-21

First public release.

### Added
- **Android app** (`android/`): the web app in a native shell, with scan-to-pair, the sign-in encrypted by the Android Keystore, and notifications for goals waiting for approval, finished goals and training decisions. See [docs/MOBILE.md](docs/MOBILE.md). Not yet run on a real device.
- **Durable jobs** (`libra jobs`, `/v1/jobs`): goals that survive restarts, pause for approval and end with a checked result.
- **Per-goal permissions**, and outside content (web, MCP/API, memories) can no longer drive a change without a person.
- **Benchmarks** (`libra eval`): planner tool choice, held-out loss, behaviour by multiple choice, and a history. Training can require the behaviour score not to drop.
- **Per-device tokens** (`libra devices`), revocable one by one; pairing links never carry the main token.
- Publishing: issue and pull-request templates, `templates/`, `scripts/release.sh`, and a release workflow that publishes the Python packages and the APK with checksums.

### Security
- Web fetching connects to the address it checked (closes DNS rebinding); a deny rule always wins; audit logs are redacted at any depth; connection, rate and input limits on the server; no CORS unless an origin is listed.

### Fixed
- Generating a reply no longer switches off gradients for training in the same process; web-triggered training runs in its own process.
- Checkpoints are published atomically, so a reload never sees new weights with an old tokenizer.
- Without a held-out split, training reports training loss instead of calling it validation loss.
- Goals with quoted text containing several sentences are no longer split inside the quote.

## [0.11.0] and earlier

Development before the first public release: the model, autograd engine, tokenizer and training; GPU support; dataset builder; skills, plugins, the skill catalog, MCP and OpenAPI; grounded answers from files; translation; unattended web training; the Debian installer with Tailscale; the phone web app; the goal agent, autonomous mode, memory and the Discord bot.
