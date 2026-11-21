---
name: reducer
description: Finds and removes moving parts in existing Libra code (branches, state, layers, duplicated logic, scripts) without changing what it does. Records the supported behaviour first, derives the smallest mechanism, and proves the behaviour is unchanged. Use when code, a feature or a PR feels more complicated than what it does, or to review a change for complexity. Pass "diagnose" to only report.
argument-hint: "[diagnose] <file, module, feature or PR>"
---

Reduce `$ARGUMENTS`. With `diagnose`, report the design and change nothing.

## 1. Behaviour ledger (before any edit)

List every behaviour the code supports today. For each one, give the test that proves it (`tests/…::name`), or write `no test`. Include the quiet ones: error messages, exit codes, limits, file formats on disk under `~/.libra`, API status codes (see the `api-conventions` skill), and trust/approval behaviour.

Removing a supported behaviour is a **scope change**, not a reduction. List it separately as a proposal for the user to decide, and don't do it.

## 2. Find the moving parts

Count what the behaviour actually needs against what is there:

- **Branches and modes** nobody uses: check callers with `grep -rn`, the CLI in `libra/cli.py`, routes in `libra/server.py`, and the app.
- **State kept in two places**, or derived state that's also stored.
- **Layers that only pass calls through.**
- **The same logic written twice**, e.g. in the CLI and the server, or in `agent.py` and `jobs.py`.
- **Hand-written code that an existing Libra function already does** (`home.path`, `checkpoint`, `jobs`, `web.Fetcher`, `trust`).

## 3. The smallest mechanism

Describe the design with the fewest parts that still satisfies every ledger row. Name what goes, what merges, and what stays.

## 4. Change in slices (unless `diagnose`)

- One reversible step at a time. After each step, the ledger's tests and the `verify` skill pass. Add a test first for any ledger row marked `no test` that the slice touches.
- One commit per slice, saying what was removed and which tests prove nothing changed.
- Stop and ask if a slice would need a ledger row to change.

## Report

Give the ledger, the parts removed (with line counts before and after), the tests that prove the behaviour, and any proposed behaviour changes left for the user. For *new* work, use the `yagni` skill instead.
