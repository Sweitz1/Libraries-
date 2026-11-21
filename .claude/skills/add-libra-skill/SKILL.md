---
name: add-libra-skill
description: Adds a new skill (tool) to Libra, either built in or as a plugin, with the right risk level, outside-content marking, router, tests and planner coverage. Use when asked to add, write or build a Libra skill, tool or plugin (Libra's own skills, not Claude Code skills).
argument-hint: "[what the skill should do]"
---

## The rules a Libra skill follows

- **Original code, no new dependencies.** NumPy and the standard library only, and no outside AI services. Web access goes through `libra.web.Fetcher`, which applies robots.txt, rate limits and the private-address guard. Never use `urllib` directly.
- **Risk is honest.**
  - `risk="read"` only looks things up.
  - `"write"` changes something. It is the default, and people are asked first.
  - `"danger"` is hard to undo.
  - Never mark something `read` to avoid the approval prompt.
- **Outside content is marked.** A skill that returns text from a website, another service or stored earlier results gets `external=True` (built-ins: add its name to `EXTERNAL_BUILTINS` in `libra/skills.py`). That output can then never drive a change without a person approving it.
- **Results are useful to later steps.** Return a string, or a dict or list for structured data. `"each of them"` goal steps iterate over a list's `url`, `link` or `text`.
- **Failures are words, not crashes.** Raise with a clear message; `SkillRegistry.run` turns it into a failed result.

## Where it goes

- **Generally useful, and fits the rules:** a built-in in `_register_builtins` in `libra/skills.py`, using `@reg.skill(name, description, params, router=…, risk=…)`.
- **Specific to one person or service:** a plugin. Copy `templates/plugin.py` into `plugins/` or `~/.libra/plugins/`.
- **An existing MCP server or OpenAPI spec:** no code needed. Use `libra mcp add …` or `libra api add …`.

The **router** is optional. `router(message) -> dict | None` recognises a chat message or goal step and fills the arguments. Without one, the planner matches by name and description words, accepting a match only if they cover at least half the step's key words. Write the description in the words people use.

## Test it

1. **Unit test** in `tests/`: call it via `default_registry(log=None, load_default_dir=False, catalog=None)` and `reg.run(name, args)`. For network skills, serve pages from a local `ThreadingHTTPServer` and pass `allow_private` (see `tests/test_web.py`).
2. **Trust:** a write or danger skill must stop at approval:
   - `libra.jobs` gives status `waiting`;
   - `agent.execute` gives `blocked`.
3. **Outside content:** if `external`, a write step consuming its output must wait. See `PermissionsAndOutsideContentTest` in `tests/test_jobs.py`.
4. **Planner:** add the goals people would type to `libra/evals/planner.jsonl` (built-ins) and run `libra eval plan`. The score must not drop below the current baseline (27/28).
5. Run the `verify` skill.

## Document it

Add a row to the built-in skills table in `README.md` (*Skills and plugins*), list it in `docs/REVIEW.md` if it's a built-in, and add a `CHANGELOG.md` line under *Unreleased*.
