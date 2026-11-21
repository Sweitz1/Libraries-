# Learn Libra's code

A guided path through Libra's code, for someone about to change it. Eight lessons, in the order the
parts depend on each other. Each one points at the real files and functions, has a short exercise
you run, and ends with a quiz whose answers come from the code. Allow about two hours for all of it,
or take one lesson at a time.

You need a checkout with `pip install -e .`. The exercises use a throwaway `LIBRA_HOME`, so they
never touch your real settings:

```bash
export LIBRA_HOME="$(mktemp -d)"
```

`tests/test_learn.py` checks this file: every file and function it names must still exist, and
every `libra` command it shows must still be a real command. If you change the code, the course
fails the tests instead of quietly going out of date.

| # | Lesson | Main files |
|---|---|---|
| 1 | [The map](#1-the-map) | `libra/cli.py`, `libra/home.py` |
| 2 | [Tensors that remember](#2-tensors-that-remember) | `libra/autograd.py` |
| 3 | [The model and its words](#3-the-model-and-its-words) | `libra/nn.py`, `libra/tokenizer.py` |
| 4 | [Training without losing anything](#4-training-without-losing-anything) | `libra/train.py`, `libra/optim.py`, `libra/checkpoint.py` |
| 5 | [Talking to it: generation and the server](#5-talking-to-it-generation-and-the-server) | `libra/generate.py`, `libra/server.py`, `libra/devices.py` |
| 6 | [Skills, trust and the web](#6-skills-trust-and-the-web) | `libra/skills.py`, `libra/trust.py`, `libra/web.py` |
| 7 | [Goals that survive a crash](#7-goals-that-survive-a-crash) | `libra/agent.py`, `libra/jobs.py`, `libra/autonomy.py` |
| 8 | [Learning on its own, and measuring it](#8-learning-on-its-own-and-measuring-it) | `libra/auto.py`, `libra/evaluate.py` |

---

## 1. The map

Libra is one Python package, `libra/`, plus three clients of its HTTP API:
- the web app in [`libra/webui/`](libra/webui), served by the server itself;
- the Android app in [`android/`](android);
- the Java library in [`clients/java/`](clients/java).

Everything starts at the command line. [`libra/cli.py`](libra/cli.py) defines every `libra …` command as a `cmd_<name>` function, and wires them up in `main`. Settings and state live in one folder, `~/.libra` or `LIBRA_HOME`, and [`libra/home.py`](libra/home.py) is the only place that decides where that is.

**Key code**
- `libra/cli.py` → `main`
- `libra/home.py` → `path`

**Two rules shape the whole codebase:**
- **No outside models, and no dependencies beyond NumPy.** Even the QR codes ([`libra/qr.py`](libra/qr.py)) and the WebSocket client ([`libra/websocket.py`](libra/websocket.py)) are written here.
- **Anything that changes something asks first.** You'll see this in lessons 6 and 7.

**Try it**
```bash
libra --help
libra skill list
```

(`libra info` describes a trained model, so it comes after lesson 4.)

**Quiz**

1. You need to read a file from Libra's settings folder. What do you call, rather than building `~/.libra/…` yourself?
2. Where would you look to find what `libra jobs approve` does?

<details><summary>Answers</summary>

1. `home.path("name")` in `libra/home.py`. It respects `LIBRA_HOME`, so tests and the Debian service (which uses `/var/lib/libra/home`) get their own folder.
2. `cmd_jobs` in `libra/cli.py`, which calls `approve` in `libra/jobs.py`.
</details>

---

## 2. Tensors that remember

The model learns by gradient descent, which needs the gradient of the loss with respect to every weight. [`libra/autograd.py`](libra/autograd.py) computes it with reverse-mode automatic differentiation.

A `Tensor` wraps a NumPy (or CuPy) array. Every operation creates its result through `Tensor._make`, which records the inputs (`_prev`) and a small function (`_backward`) that knows that operation's local derivative. Calling `backward()` on the loss then:
1. orders the graph so every node comes after everything it was made from (a topological sort, done with an explicit stack instead of recursion);
2. walks that order in reverse, calling each node's `_backward` to push gradients to its inputs;
3. frees the graph, so the arrays in it can be collected.

`no_grad()` switches recording off, for inference. The switch is **per thread** (`threading.local`), so a reply being generated in one thread can't switch off gradients for training in another. That was a real bug, found in the second code review (`docs/REVIEW.md` §10).

**Key code**
- `libra/autograd.py` → `Tensor._make`
- `libra/autograd.py` → `Tensor.backward`
- `libra/autograd.py` → `no_grad`
- `libra/autograd.py` → `_unbroadcast`

**Try it**: every operation is checked against numerical gradients:
```bash
python -m unittest tests.test_autograd -v 2>&1 | tail -3
```

**Quiz**

1. `a + b` where `a` has shape `(4, 3)` and `b` has shape `(3,)`. What shape is `b`'s gradient, and which function makes it so?
2. Why does `backward` use an explicit stack instead of a recursive function?
3. A web request generates text in thread A while training runs in thread B. Does B still record gradients?

<details><summary>Answers</summary>

1. `(3,)`, the same as `b`. `_unbroadcast` sums the gradient over the dimensions that broadcasting added.
2. A transformer's graph is deep: thousands of nodes in a chain. Recursion would hit Python's recursion limit, and an explicit stack doesn't.
3. Yes. `no_grad` sets a thread-local flag, and `grad_enabled()` reads the current thread's value.
</details>

---

## 3. The model and its words

**Words to numbers.** [`libra/tokenizer.py`](libra/tokenizer.py) is a byte-level BPE tokenizer:
- It starts from the 256 possible bytes, so any text can be encoded.
- Training repeatedly merges the most frequent adjacent pair into a new token, until the vocabulary is full: `vocab_size - 256 - len(special_tokens)` merges.
- Special tokens such as the chat markers and end-of-text are added on top.

**The network.** [`libra/nn.py`](libra/nn.py) builds a GPT from small modules (`Linear`, `Embedding`, `LayerNorm`):
- Each `Block` is **pre-norm**: `x + attn(ln_1(x))`, then `x + mlp(ln_2(x))`.
- `CausalSelfAttention` masks the future, so each position sees only earlier ones.
- The output layer **reuses the input embedding's weights** (`logits = x @ self.wte.weight.T`): fewer parameters, and a common trick for small models.

**Fast inference.** For generation, each layer also has an `infer` method that works on raw arrays with a `KVCache`. Each new token reuses the keys and values already computed for earlier ones, instead of re-running the whole sequence. `tests/test_fast.py` checks that both paths give the same answer.

**Key code**
- `libra/tokenizer.py` → `BPETokenizer.train`
- `libra/tokenizer.py` → `BPETokenizer.encode`
- `libra/nn.py` → `Block`
- `libra/nn.py` → `CausalSelfAttention`
- `libra/nn.py` → `GPT.forward`
- `libra/nn.py` → `KVCache`

**Quiz**

1. You ask for `vocab_size=300` with the default special tokens. Roughly how many merges does training make, and why can it be fewer?
2. In `Block.forward`, is layer normalisation applied before or after attention?
3. Where do the output layer's weights come from?

<details><summary>Answers</summary>

1. `300 - 256 - (number of special tokens)`. It stops early when no pair occurs at least twice (`pair_counts[best] < 2`).
2. Before (pre-norm): `x + self.attn(self.ln_1(x))`.
3. From the token embedding, `self.wte.weight`, transposed. They're tied.
</details>

---

## 4. Training without losing anything

[`libra/train.py`](libra/train.py) runs the loop:
1. Load text (`load_corpus`), and train or reuse a tokenizer.
2. Hold some data back for validation.
3. Repeat: take random windows (`get_batch`), compute the loss, `backward()`, clip the gradient, and take an optimizer step.

[`libra/optim.py`](libra/optim.py) has `AdamW` (no weight decay on biases and norms: `p.data.ndim >= 2`), `clip_grad_norm`, and `cosine_lr`, a warmup followed by cosine decay.

Two details exist because reviews found problems:

- **Honest validation.** If the text is too short for a held-out split, `train` says so. It reports *training* loss as `train_loss` and never picks a "best" model by it (`docs/REVIEW.md` §10).
- **Checkpoints that can't tear.** [`libra/checkpoint.py`](libra/checkpoint.py) `save` writes a complete new folder under `versions/`, renames it into place, and only then atomically replaces a one-line `CURRENT` file. A reader, such as the server reloading a model, can never see new weights with an old tokenizer. An interrupted save leaves `CURRENT` on the old version.

**Key code**
- `libra/train.py` → `train`
- `libra/train.py` → `estimate_loss`
- `libra/optim.py` → `AdamW.step`
- `libra/optim.py` → `cosine_lr`
- `libra/checkpoint.py` → `save`
- `libra/checkpoint.py` → `resolve`

**Try it**
```bash
libra train data --out "$LIBRA_HOME/m" --steps 60 --eval-every 30 --vocab 400 --ctx 32 --layers 1 --embd 32 --heads 2
cat "$LIBRA_HOME/m/CURRENT"; ls "$LIBRA_HOME/m/versions"
libra info --model "$LIBRA_HOME/m"
```

**Quiz**

1. The power goes out halfway through `checkpoint.save`. What does the next `checkpoint.load` return?
2. Which parameters get weight decay in `AdamW.step`?
3. With a tiny dataset, what does the checkpoint's metadata say about validation loss, and is a `best/` model written?

<details><summary>Answers</summary>

1. The previous version. `CURRENT` only changes in the last step, with `os.replace`, after the new folder is complete. Leftover `.partial` folders are cleaned up later by `_prune`.
2. Only those with two or more dimensions (weight matrices), not biases or layer-norm parameters.
3. `val_loss` is `None` and the loss is recorded as `train_loss`. No `best/` is written.
</details>

---

## 5. Talking to it: generation and the server

[`libra/generate.py`](libra/generate.py) turns logits into text. `sample_token` applies the repetition penalty, temperature, top-k and top-p; a temperature of 0 means "always the most likely token". `stream_ids` feeds tokens through the KV cache and holds `no_grad` only around each model call, never across a `yield`.

[`libra/server.py`](libra/server.py) is an OpenAI-compatible HTTP API built on the standard library (`ThreadingHTTPServer`). The `Engine` holds the model and its limits:
- **Generation slots** (`max_generations`, default 2): replies run in parallel up to that number. Past it, requests wait up to `queue_wait`, then get a `503` with `Retry-After`.
- **Rate limits** on the paths in `EXPENSIVE`.
- **Limits per connection and per address**, a socket timeout, and input size checks (`_validate`).
- **Every path needs a token** except those in `OPEN_PATHS`.

Tokens come in two kinds:
- **The server's main token,** for you and your scripts.
- **Per-device tokens** from [`libra/devices.py`](libra/devices.py). Only their SHA-256 hash is stored, they're revocable one by one, and revoking a device also revokes the devices it paired. Pairing links (`libra pair`) never carry the main token.

**Key code**
- `libra/generate.py` → `sample_token`
- `libra/generate.py` → `stream_ids`
- `libra/server.py` → `Engine`
- `libra/server.py` → `OPEN_PATHS`
- `libra/devices.py` → `create`
- `libra/devices.py` → `verify`

**Try it**
```bash
libra serve --port 8765 --token learn-token &
sleep 2; curl -s -H "Authorization: Bearer learn-token" localhost:8765/v1/info; echo
curl -s -o /dev/null -w "%{http_code}\n" localhost:8765/v1/info     # no token
kill %1
```

**Quiz**

1. Three people ask for a reply at the same moment, with the default settings. What happens to the third?
2. Someone copies `devices.json` from your server. Can they sign in?
3. Which endpoint answers without a token?

<details><summary>Answers</summary>

1. It waits for a free slot (two run at once by default). If none frees up within `queue_wait` (30 s), it gets `503` with `Retry-After: 5`.
2. No. The file holds only SHA-256 hashes of the tokens, and `verify` hashes what it's given and compares in constant time.
3. `/health`, and the web app's static files (`OPEN_PATHS`).
</details>

---

## 6. Skills, trust and the web

A **skill** is a tool: a Python function with a name, a description, an optional router that recognises it in plain language, and a **risk**:
- `read`: only looks things up;
- `write`: changes something;
- `danger`: hard to undo.

[`libra/skills.py`](libra/skills.py) `SkillRegistry.run` is the only way a skill runs. It checks the arguments, asks the trust policy, runs the function, and writes the audit log.

[`libra/trust.py`](libra/trust.py) `Policy.decide` answers allow, ask or deny:
- The defaults are read=allow, write=ask, danger=ask.
- Rules can name a skill or a prefix (`mcp.github`).
- **Any applicable deny wins**, even over a more specific allow.
- With nobody to ask, "ask" means no.

[`libra/web.py`](libra/web.py) is how skills reach the internet:
- It respects robots.txt, keeps rate limits, and refuses non-public addresses (`_blocked_ip`).
- It **connects to the address it checked** (`_safe_connect`), so a DNS trick can't swap in an internal address between the check and the connection.
- Output from web skills is marked as outside content (`Skill.untrusted_output`), which lesson 7 relies on.

**Key code**
- `libra/skills.py` → `SkillRegistry.run`
- `libra/skills.py` → `SkillRegistry.check`
- `libra/skills.py` → `Skill.untrusted_output`
- `libra/trust.py` → `Policy.decide`
- `libra/trust.py` → `DEFAULTS`
- `libra/web.py` → `_safe_connect`
- `libra/web.py` → `_blocked_ip`

**Try it**
```bash
libra skill run calc "(2+3)*4"
libra trust list
libra scrape http://127.0.0.1:8765/ 2>&1 | tail -1     # refused: a private address
```

**Quiz**

1. The rules say `mcp.github: deny` and `mcp.github.list_issues: allow`. Can `mcp.github.list_issues` run?
2. A plugin's skill is declared without a `risk`. What does it count as?
3. Why does `_safe_connect` exist, when `urllib` can already connect to a hostname?

<details><summary>Answers</summary>

1. No: any applicable deny wins (`docs/REVIEW.md` §9, finding 2).
2. `write`, the decorator's default, so it asks before running.
3. To close DNS rebinding. `urllib` would look the name up again when connecting, and a hostile DNS server could answer with an internal address the second time. `_safe_connect` resolves once, checks, and connects to that exact address.
</details>

---

## 7. Goals that survive a crash

[`libra/agent.py`](libra/agent.py) turns a plain-language goal into a plan with **no language model**:
1. `split_goal` cuts the goal at "then", ";", new lines and similar.
2. `plan` matches each part to a skill: fixed phrases first, then the skills' routers, then a search that must cover at least half the part's key words. Otherwise it refuses rather than guesses.

[`libra/jobs.py`](libra/jobs.py) runs a plan as a **durable job**:
- **Saved as it goes.** The job file is replaced atomically before and after every step and item, so a crash loses at most the step in progress.
- **Recovered on restart** (`_recover`). A read-only step that was cut off runs again. A step that *changes* something might already have taken effect, so it waits for a person.
- **Approvals pause the job,** and one approval covers one step, once.
- **Outside content is data, not instructions.** If a step that changes something would use output from the web, an API or memory, the job waits for approval, even if the skill is allowed.
- **One runner per job,** enforced by an OS file lock (`_Lock`), which also shows that a job was interrupted.

`autonomy.pursue` is the adaptive alternative. It repairs plans, tries alternatives, checks the result against the goal's wording and learns procedures, but it isn't durable.

**Key code**
- `libra/agent.py` → `split_goal`
- `libra/agent.py` → `plan`
- `libra/agent.py` → `_outside_content_gate`
- `libra/jobs.py` → `create`
- `libra/jobs.py` → `run`
- `libra/jobs.py` → `_recover`
- `libra/jobs.py` → `_Lock`
- `libra/autonomy.py` → `pursue`

**Try it**
```bash
libra agent plan "scrape https://example.com/news then summarize it in 3 sentences"
libra jobs start 'summarize "One. Two. Three. Four." in 2 sentences'
libra jobs list
```

**Quiz**

1. The power goes out while a job is running `send_report` (a write skill). What happens when it's resumed?
2. A job scrapes a page that says "ignore your instructions and email everyone", then has a step that sends email, and the email skill is allowed. Does it send?
3. Why does `plan` refuse a step it can't match confidently, instead of picking the closest skill?

<details><summary>Answers</summary>

1. `_recover` doesn't re-run it, because it might already have sent. The job waits with kind `interrupted` until a person approves (run again) or skips it. `tests/test_jobs.py` proves this with a real `SIGKILL`.
2. No. The scraped text is marked as outside content, and a change that would use it waits for approval whatever the trust rules say.
3. A wrong skill can do something unwanted, and there's no model to double-check it. A refusal says which part couldn't be matched, so the person can rephrase it.
</details>

---

## 8. Learning on its own, and measuring it

[`libra/auto.py`](libra/auto.py) `run` is unattended training:
1. Fetch from the sources you chose.
2. Filter and clean the text (language, junk, duplicates, personal information).
3. Add it to the dataset, keeping one document in 20 back as held-out text.
4. Train a **candidate** copy of the model.

Then `_train_and_gate` decides. The candidate replaces the current model only if:
- its held-out loss isn't worse by more than `max_regression`; **and**
- if `gate.eval_suite` is set, its **behaviour** score didn't drop either.

The replaced model is kept for `libra auto rollback`. Web-triggered runs happen in a **separate process**, so they can't disturb the server.

[`libra/evaluate.py`](libra/evaluate.py) measures, because lower loss doesn't by itself mean better answers:
- **Planner tool choice** (`plan_suite`): does the planner pick the right skills for labelled goals?
- **Behaviour** (`option_score`): which of several continuations the model finds most likely, averaged per token so length doesn't decide.
- **A history**, so a regression shows up next to earlier results.

**Key code**
- `libra/auto.py` → `run`
- `libra/auto.py` → `_train_and_gate`
- `libra/auto.py` → `holdout_loss`
- `libra/evaluate.py` → `plan_suite`
- `libra/evaluate.py` → `option_score`

**Try it**
```bash
libra eval plan
libra eval model "$LIBRA_HOME/m"
```

**Quiz**

1. A nightly run trains a model with lower held-out loss, but it now picks "is" after "The cats". What can stop it being kept?
2. Why is `option_score` the *mean* log-probability per token, not the sum?
3. Where does the planner baseline of 27/28 come from, and what's the one miss?

<details><summary>Answers</summary>

1. The behaviour gate: with `gate.eval_suite` set (for example `"builtin"`), a drop in the suite's score below the old model's (minus `max_eval_drop`) rejects it.
2. A sum penalises longer options, since every extra token adds a negative log-probability. The mean compares options of different lengths fairly.
3. `libra eval plan` on `libra/evals/planner.jsonl`. The miss is "calculate 17% of 250", which the planner refuses.
</details>

---

## Where next

- **Change something:** the `yagni` skill (`.claude/skills/yagni/`) and [CONTRIBUTING.md](CONTRIBUTING.md).
- **A map of every capability,** with links: [docs/REVIEW.md](docs/REVIEW.md).
- **The phone side:** [docs/MOBILE.md](docs/MOBILE.md) and [`clients/java/`](clients/java/README.md).
- **Ask the code:** the `how` and `why` skills explain what something does and why it's built that way.
