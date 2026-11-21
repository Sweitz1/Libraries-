---
name: why
description: Recovers why a piece of Libra's code or a rule exists in its current form, from the git history, the review responses in docs/REVIEW.md, the changelog, tests and comments. Labels each finding as fact, inference or unknown. Read-only. Use for "why does X exist", "why was this changed", "can we remove X", or before simplifying code you don't understand.
argument-hint: "<code, rule or behaviour>"
---

Find out why `$ARGUMENTS` is the way it is. Read only: change nothing.

## Where Libra's reasons are written down

1. **`docs/REVIEW.md` §9 onwards.** Four external reviews were answered point by point. Many protections exist because of one of them: the pinned DNS connection, deny-wins trust, device tokens, per-thread gradients, atomic checkpoints, outside-content marking.
2. **`CHANGELOG.md`**, and the commit messages, which explain the reason as well as the change:
   ```bash
   git log --follow --format='%h %ad %s' --date=short -- <file>
   git log -S '<a distinctive line>' --format='%h %s' -- <file>   # when a line appeared or went
   git blame -L <start>,<end> <file>
   git show <commit>
   ```
3. **The tests that pin it.** A test's name and docstring often state the reason (`grep -rn <name> tests/`). A test that fails without the code is the strongest evidence that it's needed.
4. **Comments next to the code**, and the README section about that feature.
5. **Pull requests**, when a commit message refers to one.

## Label every finding

- **Fact:** stated in a commit, review response, changelog, test or comment. Quote it with its source (`abc1234`, `REVIEW.md §10`, `tests/test_x.py::name`).
- **Inference:** your reading of the evidence. Say what it's based on.
- **Unknown:** no record found. Say where you looked.

## Answer

1. **The reason, in one or two sentences, with its label.**
2. **The evidence trail**, oldest first.
3. **"Can it go?"** If the question was about removing it, say which tests or behaviours depend on it, and whether the original reason still holds. The decision is the user's. To actually simplify it, use the `reducer` skill.

For *what* the code does now, use the `how` skill.
