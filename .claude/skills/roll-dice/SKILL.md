---
name: roll-dice
description: Roll dice using a random number generator. Use when asked to roll a die (d6, d20, etc.), roll dice, or generate a random dice roll, including notation like 2d6 or 3d20+4.
---

To roll dice, run `scripts/roll.py` from this skill's base directory (the folder shown when the skill
loads). It understands standard dice notation and picks every roll uniformly at random (Python's
`secrets.randbelow`):

```bash
python3 <skill folder>/scripts/roll.py 2d6        # two six-sided dice
python3 <skill folder>/scripts/roll.py d20 3d4+2
```

It prints each roll and the total, e.g. `2d6: 3 + 5 = 8`. Notation is explained in
[references/notation.md](references/notation.md).

If Python isn't available, roll one die with the shell. Replace `<sides>` with the number of sides:

```bash
echo $((RANDOM % <sides> + 1))
```

```powershell
Get-Random -Minimum 1 -Maximum (<sides> + 1)
```

`$RANDOM % <sides>` is very slightly uneven, because `$RANDOM` stops at 32767, which most die sizes don't divide. That's fine for a game; use the script when fairness matters.

Report the individual rolls as well as the total. Never make up a roll: always run a command.
