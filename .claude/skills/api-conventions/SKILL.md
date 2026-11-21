---
name: api-conventions
description: API design patterns for Libra's HTTP server (libra/server.py) and its clients. Covers paths, auth, error format, status codes, validation, limits and trust. Use when adding or changing an endpoint, or code that calls the API (the web app, the Android app, the Java client).
---

When writing API endpoints for Libra, follow what the server already does, so every client (the web app, Android, `clients/java`, and OpenAI-compatible apps) keeps working.

## Naming

- **RESTful paths under `/v1/`**: a plural noun for a collection, and an id for one item. `GET /v1/jobs` lists, `POST /v1/jobs` creates, `GET /v1/jobs/<id>` reads one.
- **Actions on an item** are `POST /v1/<things>/<id>/<verb>` (`/v1/jobs/<id>/approve`, `skip`, `cancel`, `resume`). GET never changes anything.
- **OpenAI-compatible endpoints keep OpenAI's shapes exactly** (`/v1/chat/completions`, `/v1/completions`, `/v1/models`, server-sent events). Libra-only options go in `libra_*` fields (`libra_auto_skills`, `libra_mode`), never by changing standard fields.
- **Validate ids** before they reach the file system. Jobs, for example, match `[0-9]{8}-[0-9]{6}-[0-9a-f]{6}`, so a path like `../../etc` is a 404, not a file read.
- **Bodies** are JSON objects, **query strings** are only for filters (`?n=20`, `?q=…`), and **numbers are clamped** (`max(1, min(n, 200))`).

## Errors: always the same format

Use `self._error(status, message, headers)`, which sends:

```json
{"error": {"message": "what went wrong, in plain words", "type": "invalid_request_error"}}
```

The message tells a person what to do next. Never put a stack trace, a token or a file path from the server in it.

| Status | When |
|---|---|
| 400 | Bad or missing fields, values over the limits |
| 401 | No token, wrong token, or revoked device token (repeated failures from one address are locked out: 429) |
| 403 | A skill needs approval or is denied by the trust rules. Send the skill result (`ok: false`, `text`) so the client can show why |
| 404 | Unknown path, skill or id, or a feature this server wasn't started with |
| 405 | Right path, wrong method. Say which one to use |
| 409 | A conflict with current state: already running, not waiting |
| 413 | The body is too large (`MAX_BODY`; the server reads and discards the rest) |
| 429 | Rate limit. Include `Retry-After` |
| 503 | Busy, or no model loaded. Include `Retry-After` when retrying will help |

Clients rely on these codes: `clients/java` treats 0, 429 and 503 as temporary, and Android shows "signed out" on 401.

## Validation and limits

- **Check sizes in `_validate(path, body)`**, using the `LIMITS` dict, before doing any work.
- **An endpoint that does expensive work** (generation, translation, skills, agents, jobs, training) goes in `EXPENSIVE`, which applies the per-address rate limit.
- **Long work runs in the background**: start a thread or a job, reply `202` with what was started (`{"started": true, "job": …}`), and let the client poll. Never hold a request open for minutes.
- **Generation goes through the slots** (`engine.run`/`_respond`), never around them.

## Auth and trust

- **Every path needs the bearer token except `OPEN_PATHS`** (`/health` and the app's static files). Don't add to `OPEN_PATHS` without a reason written next to it.
- **Browsers sign in with a session cookie** (`libra/sessions.py`): `POST /v1/session` with a bearer token sets it, and `self.via_cookie` is then true. A `POST` with the cookie needs `X-Libra-CSRF: 1` and a same-host `Origin` (`_same_site`); keep `GET`s read-only so the cookie can't be used to change anything without that check. Never accept a token from a cookie, only a session id.
- **`self.principal` is `"owner"` or `"device:<id>"`.** Record it on anything a person decides (approvals, pairings), and limit device tokens to what their own device needs (see `_revoke`).
- **Anything that runs a skill goes through `SkillRegistry.run`** or `libra.jobs`, never `skill.func` directly, so the trust rules, the audit log and the outside-content gate apply.
- **No CORS headers** unless the origin is in `engine.cors_origins`.

## When you add an endpoint

1. Add it to the docstring at the top of `libra/server.py`.
2. Add a test in `tests/`. Start a real server with `make_server(Engine(...), "127.0.0.1", 0)` (see `tests/test_hardening.py` and `tests/test_jobs.py`). Cover success, a 400, a 401 and the trust case.
3. If clients use it, add it to `clients/java` (`LibraClient`, plus `LiveServerTest`), and to the app contract table in `docs/MOBILE.md`.
4. Run the `verify` skill.
