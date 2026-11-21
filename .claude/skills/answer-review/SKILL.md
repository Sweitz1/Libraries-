---
description: Works through an external code review or audit of Libra point by point. It checks each claim against the current code, reproduces real bugs before fixing them, fixes them with tests that fail on the old code, and records the response in docs/REVIEW.md. Use when the user pastes a review, audit, security report or roadmap about Libra.
---

## Know what the review looked at

Reviews often read an **old commit**, because a link like `blob/<sha>/…` pins a version. Find the commit it cites and compare it with now:

!`git log --oneline -1`

!`grep -n "^## [0-9]*\. Response" docs/REVIEW.md`

Earlier responses are in those sections. Points already fixed there are answered by pointing at that section, not fixed twice.

## For each point

1. **Check it in the current code.** Read the file and function it names. Classify it as: real now / already fixed (cite the section) / partly true / not applicable (explain).
2. **Reproduce real bugs first.** Write the test that shows the problem and watch it fail on the current code, for example a rebinding resolver, a thread pausing mid-generation, or a `kill -9` partway through a job. Record what the failure looked like.
3. **Fix the cause** in the smallest coherent change, keeping Libra's rules: no new dependencies, no outside models, and trust rules on every path. Prefer the safe default, for example refusing or waiting for a person, over a configurable bypass.
4. **Re-run** the test (it now passes), then the `verify` skill.
5. **Suggestions that aren't bugs** (a roadmap, "add X") are built only when they're clearly in scope or the user asks. Otherwise list them as not done, with one line on what it would take.

## Record the response

Add a section to `docs/REVIEW.md` named `## N. Response to the <ordinal> review (<what it was>)`. It should have:

- which commit the review read;
- a table with one row per point, giving its status and the fix with links to the code and the test names;
- **residual risks** that remain, stated plainly.

Also update the capability and security tables if behaviour changed, the test counts in section 1, and `CHANGELOG.md`. Check that the relative links resolve.

## Tell the user

One short paragraph per point: what was true, what changed, what's still open. Say which points only a real deployment can settle (GPU, Debian with systemd, Tailscale, Discord, a real phone) instead of implying they're covered.
