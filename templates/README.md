# Templates

Copy one of these to start something of your own.

| Template | For | Use it |
|---|---|---|
| [`plugin.py`](plugin.py) | Your own skills (tools) | copy to `~/.libra/plugins/`, or load with `--plugins <folder>` |
| [`eval-suite.jsonl`](eval-suite.jsonl) | Your own model benchmark | `libra eval model <checkpoint> --suite eval-suite.jsonl` |
| [`planner-suite.jsonl`](planner-suite.jsonl) | Checking the planner picks your skills | `libra eval plan --suite planner-suite.jsonl --plugins <folder>` |
| [`auto.json`](auto.json) | Unattended training from websites | edit `sources`, then `libra auto run auto.json` |

For a GitHub issue or pull request, the forms in [`.github/`](../.github) are used automatically.
