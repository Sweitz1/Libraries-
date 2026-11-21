---
name: yagni
description: Keeps a change to Libra's production code at the minimum the requirement needs. States a short plan (goal, non-goals, acceptance criteria, what stays untouched), writes the failing test first, and asks instead of guessing a missing requirement. Use before implementing a feature, a fix or a review suggestion in libra/, android/ or clients/.
---

Before editing production code, write this plan in the conversation (a few lines each):

1. **Goal:** the one behaviour that must change, in the user's words.
2. **Non-goals:** nearby things this change will *not* do, even if tempting (options, refactors, "while I'm here").
3. **Acceptance:** the checks that prove it, as test names or commands. At least one must fail before the change.
4. **Untouched:** files and behaviours that must stay as they are.

If a requirement is missing or ambiguous (a limit, an error message, who may call it), ask. Don't pick a value silently.

## While building

- **Test first.** Write the failing test, watch it fail for the right reason, then make it pass. For a bug, reproduce it (`tests/`) before fixing it.
- **The smallest mechanism that passes:**
  - no new module when a function in an existing one fits (see `docs/REVIEW.md` §4 for where things live);
  - no new option, setting or CLI flag that nobody asked for;
  - no new abstraction for a single use.
- **Libra's fixed limits are not negotiable:**
  - no new dependencies (NumPy and the standard library; the Android app uses only the framework);
  - no pretrained models or outside AI services;
  - skills that change something are marked `write` or `danger`, and outside content stays marked.
- **Leave the rest alone.** Reformatting, renaming and "improving" code outside the change make it harder to review. Note such ideas for later instead.

## Before calling it done

- Every acceptance check passes. Run the `verify` skill.
- The diff touches nothing listed under *Untouched*. If it had to, say why.
- A user-visible change has a README or `docs/` line and a `CHANGELOG.md` entry, and nothing more.

For complexity that *already exists*, use the `reducer` skill instead: `yagni` stops new complexity, `reducer` removes old.
