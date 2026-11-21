---
name: roll-dice
description: Roll dice using a random number generator. Use when asked to roll a die (d6, d20, etc.), roll dice, or generate a random dice roll, including notation like 2d6 or 3d20+4.
compatibility: Requires Python 3.9+ (no packages). Falls back to the shell without Python.
---

## Available scripts

- **`scripts/roll.py`**: rolls dice in standard notation, each roll uniformly at random (Python's `secrets.randbelow`). Run `python3 scripts/roll.py --help` for its options.

## Workflow

1. Roll, passing every requested roll at once:
   ```bash
   python3 scripts/roll.py 2d6 d20
   ```
   It prints one line per roll, e.g. `2d6: 3 + 5 = 8`. Add `--json` for `{"spec", "rolls", "modifier", "total"}` objects.
2. Exit code `2` means the notation was invalid. The error says what was received and what's expected (see [references/notation.md](references/notation.md)). Fix it, or ask the user what they meant.
3. Report the individual rolls as well as the total. Never make up a roll: always run a command.

## Without Python

Roll one die with the shell. Replace `<sides>` with the number of sides:

```bash
echo $((RANDOM % <sides> + 1))
```

```powershell
Get-Random -Minimum 1 -Maximum (<sides> + 1)
```

`$RANDOM % <sides>` is very slightly uneven, because `$RANDOM` stops at 32767, which most die sizes don't divide. That's fine for a game; use the script when fairness matters.
