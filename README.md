# Libra: a language model written from scratch

Libra is a small GPT-style language model where **every piece is original code**. It uses no pretrained weights, no downloaded model, no API key and no subscription. You train it on your own text, and it runs on your own computer.

The only dependency is **NumPy**. Everything else is in this repo:

| File | What it is |
|---|---|
| `libra/backend.py` | CPU (NumPy) or NVIDIA GPU (CuPy) array backend |
| `libra/autograd.py` | Reverse-mode automatic differentiation engine (tensors, broadcasting, matmul, softmax, layer norm, GELU, cross-entropy, embeddings, dropout) |
| `libra/nn.py` | Modules and the transformer: causal multi-head self-attention, MLP, pre-norm residual blocks, tied embeddings |
| `libra/tokenizer.py` | Byte-level BPE tokenizer trained on your corpus, with chat special tokens |
| `libra/optim.py` | AdamW, gradient clipping, warmup + cosine learning-rate schedule |
| `libra/train.py` | Data loading (`.txt`, `.md`, `.jsonl` chats), training loop, validation, checkpoints, resume |
| `libra/generate.py` | Fast sampling with a key/value cache: temperature, top-k, top-p, repetition penalty, streaming UTF-8 decode |
| `libra/dataset.py` | Dataset builder: files, chat exports, web pages and Project Gutenberg books into clean, de-duplicated training data |
| `libra/server.py` | OpenAI-compatible HTTP API (standard library only), with streaming |
| `libra/web.py` | Web fetcher (robots.txt, rate limits, private-address guard), HTML parser, CSS selector engine, scraper and crawler |
| `libra/skills.py` | Skills (tools), plugin loader, message routing, built-in skills |
| `libra/catalog.py` | Skill catalog: SQLite with an inverted BM25 index, lazy loading, schema-based argument checking |
| `libra/mcp.py` | MCP client (stdio and Streamable HTTP) that syncs server tools into the catalog |
| `libra/openapi.py`, `libra/yamlish.py` | OpenAPI/Swagger importer and caller; a small YAML reader for specs |
| `libra/trust.py` | Risk levels, allow/ask/deny rules, confirmation prompts, audit log |
| `libra/knowledge.py` | Workspace of your files: BM25 search, cited grounded answers, clarifying questions, corrections and rules |
| `libra/cli.py` | `libra data / train / generate / chat / serve / kb / skill / mcp / api / trust / scrape / crawl / info` |

Each operation in the autograd engine, and the full model, is checked against numerical gradients in `tests/`.

## Install

```bash
git clone https://github.com/Sweitz1/Libraries-.git
cd Libraries-
pip install -e .          # or just: pip install numpy, then use "python -m libra"
```

Python 3.9+.

## Quick start

```bash
# 1. Train on the bundled sample data (about 30 seconds on a laptop)
libra train data --out checkpoints/demo --steps 300 --vocab 512 --ctx 64 --layers 2 --embd 96

# 2. Chat with it
libra chat --model checkpoints/demo

# 3. Or continue a prompt
libra generate --model checkpoints/demo --prompt "The library"
```

The sample data is tiny, so the demo model mostly repeats what it saw. To get a model that writes new text, train it on more data.

## Build a dataset

`libra data` gathers text from many places, cleans it, removes duplicates and tells you what model size and step count fit:

```bash
libra data my_notes/ chats.jsonl faq.csv \
  --gutenberg 1342 --gutenberg 84 \
  --url https://example.com/article.html \
  --out datasets/mine
libra train datasets/mine ...   # use the command it suggests
```

It understands:

| Input | How it's used |
|---|---|
| `.txt` `.md` `.rst` | Plain text (Project Gutenberg headers and licences are stripped automatically) |
| `.html` `.htm` | Page text only, without scripts, menus and footers |
| `.jsonl` / `.json` with `messages` | Chats (OpenAI format) |
| ChatGPT export `conversations.json` | Chats, following each conversation's thread |
| ShareGPT (`conversations` with `from`/`value`) | Chats |
| Alpaca (`instruction`, `input`, `output`) | Chats |
| `.csv` / `.tsv` with question/answer (or prompt/response) columns | Chats; any other CSV becomes text |
| `--gutenberg N` | Downloads public-domain book number N from gutenberg.org |
| `--url URL` | Downloads a web page or text file |

The output folder holds `text.jsonl`, `chat.jsonl` and `stats.json`, with character counts, an estimated token count and the suggested `libra train` command. Only use text you have the right to use.

## Training on your own text

Point `libra train` at files or folders:

- **`.txt` / `.md`**: plain text. Books, notes, articles, code.
- **`.jsonl`**: one JSON object per line, either
  - `{"messages": [{"role": "user", "content": "..."}, {"role": "assistant", "content": "..."}]}` to teach it to chat, or
  - `{"text": "..."}` for plain documents.

```bash
libra train my_books/ my_chats.jsonl --out checkpoints/mine --steps 5000
libra train my_books/ --out checkpoints/mine --steps 10000 --resume   # keep going later
```

`checkpoints/mine/` holds the latest checkpoint and `checkpoints/mine/best/` holds the one with the lowest validation loss.

### Size guide (CPU)

| Preset | Flags | Parameters | Good for |
|---|---|---|---|
| Tiny | `--layers 2 --embd 96 --ctx 64` | ~0.3M | Testing, under 100 KB of text |
| Small (default) | `--layers 4 --embd 128 --ctx 128` | ~1M | 1–10 MB of text |
| Medium | `--layers 6 --embd 256 --heads 8 --ctx 256 --vocab 4096` | ~6M | 10 MB+ of text, overnight runs |
| Large (GPU) | `--layers 8 --embd 384 --heads 6 --ctx 256 --vocab 8192 --device gpu` | ~17M | 50 MB+ of text |

A rule of thumb: you want at least about 20 training tokens per parameter. Keep an eye on the logged `val loss`. If it rises while the training loss keeps falling, the model is memorising: add text or use a smaller model.

**What to expect:** a model this size learns the style, vocabulary and common phrases of its data. It does not know facts about the world that you don't teach it. Quality grows with the amount of text and training time.

### Useful flags

`--vocab` tokenizer size · `--ctx` context length · `--layers` `--heads` `--embd` model size · `--batch` batch size · `--steps` training steps · `--lr` peak learning rate · `--dropout` · `--eval-every` · `--resume` · `--sample-prompt` text used for the preview printed at each evaluation.

## Your files as a knowledge base (grounded answers)

Libra can answer **only from your own files**, citing exactly where each sentence came from. When the answer isn't in your files, it says so instead of guessing.

```bash
libra kb add ~/notes ~/meetings/sync.vtt        # index files and folders
libra kb ask "What is the Falcon budget?"
```
```
The Falcon budget for 2027 is 40,000 dollars, approved by Dana in October. [1] Most of it goes to cloud hosting and two contractors. [2]

Sources:
[1] notes/plan.md, line 3
[2] notes/plan.md, line 4
```

Or chat with your files. No trained model is needed for this:

```bash
libra chat --workspace ~/my-workspace     # or --grounded for the default ~/.libra/workspace
```

| Chat command | What it does |
|---|---|
| `/add <path>` | Index files or folders. Changed files are re-indexed and unchanged ones skipped |
| `/correct <answer>` | Save the right answer to your last question. It's used from then on (it survives restarts) and cited as "your correction" |
| `/rule <instruction>` | Save a standing rule, e.g. "always answer in bullet points", "keep it short", "don't ask clarifying questions" |
| `/rules`, `/sources`, `/forget <path>` | Show rules, show indexed files, remove a file |

How it behaves:

- **Your files come first.** Answers are built only from your files' sentences, found with BM25 search. Nothing comes from the model's own memory.
- **Every sentence is cited** with a file and line, or a media file and timestamp: `media/sync.mp4 (transcript: sync.vtt) @ 00:01:05–00:01:09`.
- **It doesn't guess.** If nothing relevant is found, it says so and asks which file covers the question.
- **It asks before answering when it isn't clear.** If two files answer about equally well (say, a meeting says "March 3rd" and the roadmap says "spring"), it lists both and asks which you mean. Reply with a number, a file name or "all".
- **It learns from you.** Corrections and rules are saved in the workspace folder (`corrections.json`, `rules.json`) and apply to every later answer.
- **It can match your style.** `libra kb export datasets/me` writes your files and corrections as training data. Then `libra train datasets/me` teaches a Libra model your vocabulary and tone.

Supported files: `.txt` `.md` `.html` `.csv` `.json`/`.jsonl` (including chat logs) and **`.srt`/`.vtt` transcripts with timestamps**.

**Images, audio and video:** Libra has no vision or speech model, so it can't look at a picture or listen to a recording. Put a transcript or description next to the file with the same name, for example `talk.mp4` + `talk.vtt`, or `whiteboard.jpg` + `whiteboard.txt`. Libra then indexes it and cites the media file. `/add` lists any media it found without one. PDFs aren't read yet; export them to text first.

**Limits:** Matching is by words, not meaning, so a question phrased very differently from your files may come back "not found". Rephrase it, or add a correction so that phrasing works next time. Answers quote your files rather than paraphrasing them.

The server can answer from a workspace too: `libra serve --workspace ~/my-workspace` makes `/v1/chat/completions` grounded, with citations in the reply and in a `libra_grounding` field, and adds `POST /v1/workspace/ask`, `/v1/workspace/correct` and `/v1/workspace/rule`.

## Skills and plugins

Skills are tools Libra can run: reading web pages, crawling sites, arithmetic, the time, plus anything you add with plugins. They work in four places:

| Where | Example |
|---|---|
| Chat command | `/scrape https://example.com select="h2"` or `/crawl https://example.com 5 depth=1` |
| Plain chat | `scrape https://example.com`, `crawl https://example.com`, `links on https://…`, `what is 12*7`, `what time is it` |
| Command line | `libra skill list`, `libra skill run calc "(2+3)*4"` |
| HTTP | `GET /v1/skills` (OpenAI tool schemas), `POST /v1/skills/scrape {"url": "…"}` |

When a skill runs, its output is the reply. A small model trained from scratch can't be trusted to restate web pages accurately, so it doesn't try. Turn off the plain-chat triggers with `libra chat --no-auto-skills`. On the server they're off by default; switch them on with `libra serve --auto-skills`.

Built-in skills:

| Skill | What it does |
|---|---|
| `scrape` | Page title, description, readable text (menus and scripts removed), tables, or elements matching a CSS selector. `format=markdown\|text\|json` |
| `crawl` | Same-site crawl that obeys robots.txt and waits between requests; optional `out=pages.jsonl` |
| `links` | Links on a page, optionally filtered |
| `calc` | Exact arithmetic. It parses the expression and never executes code |
| `time` | Local date and time |

### Writing a plugin

A plugin is a `.py` file. Each `@skill` function becomes a tool, and its parameters and type hints become the skill's arguments:

```python
# my_plugins/greet.py
from libra.skills import skill

@skill("greet", "Say hello to someone", params={"name": "who to greet"}, risk="read",
       router=lambda msg: {"name": msg[6:]} if msg.lower().startswith("greet ") else None)
def greet(name: str, times: int = 1) -> str:
    return " ".join([f"Hello, {name}!"] * times)
```

```bash
libra chat --plugins my_plugins/          # /greet Ana times=2   or just: greet Ana
libra serve --plugins my_plugins/ --auto-skills
```

Plugins in `~/.libra/plugins/` load automatically. Return a string, or a dict or list (shown as JSON). Mark read-only skills with `risk="read"`. The default is `"write"`, which asks before running. To fetch web pages, use `libra.skills.fetcher()` so `--allow-private` applies. See `plugins/example_tools.py`. **Plugins run with your permissions, so only load code you trust.**

## Skill catalog: MCP servers, web APIs and trust controls

Beyond the built-in skills and plugins, Libra keeps a **skill catalog**: a searchable index that can hold anything from a handful to **a million** skills. It was tested with 1,000,000 entries: 73 s to build, a 567 MB database, and 140–260 ms per search. Catalog skills are only *described* until you run one. Then the MCP server starts or the API is called.

```bash
libra skill find send email          # search everything: built-ins, plugins, catalog
libra skill info mcp.mail.send       # parameters, risk level and trust decision
libra skill run api.pets.list_pets limit=5
```

In chat, `/find <words>` searches and `/info <name>` shows details. Any catalog skill runs like a built-in, for example `/api.pets.get_pet_by_id 42`. Catalog skills are never run automatically from plain chat; you always choose one by name.

### MCP servers

Every tool of every MCP server you add becomes a skill named `mcp.<server>.<tool>`. Both MCP transports are supported: local servers over stdio, and remote ones over Streamable HTTP.

```bash
libra mcp add files -- npx -y @modelcontextprotocol/server-filesystem ~/Documents
libra mcp add remote --url https://example.com/mcp --header "Authorization: Bearer ${MY_TOKEN}"
libra mcp list
libra mcp sync all        # refresh tool lists
libra mcp remove files
```

Servers are saved in `~/.libra/mcp.json`, in the same format other MCP apps use, so you can copy entries across. `${VAR}` in headers and env values is filled from your environment, so tokens stay out of the file. Server logs go to `~/.libra/mcp-stderr.log`.

### Web APIs (OpenAPI / Swagger)

One spec file becomes one skill per endpoint, named `api.<name>.<operation>`. JSON and YAML, OpenAPI 3 and Swagger 2 are all supported.

```bash
libra api add pets ./petstore.yaml --bearer-env PET_TOKEN
libra api add weather https://example.com/openapi.json --header-env X-API-Key WEATHER_KEY
libra api add-dir ./specs/       # import a whole folder, e.g. a mirror of a public API directory
libra api list
```

A skill's arguments are the endpoint's path, query and header parameters, plus the request body's fields. **API keys are never stored.** `~/.libra/apis.json` only records which environment variable to read (`--bearer-env`, `--header-env`, `--query-env`, `--basic-env`). Many public APIs need a key or an account, so check before relying on one.

### Trust controls

Every skill has a risk level:

| Risk | Examples | Default |
|---|---|---|
| `read` | built-ins, GET requests, MCP tools marked read-only | allow |
| `write` | create, update, send, post, PUT/PATCH/POST requests | **ask** |
| `danger` | delete, pay, transfer, run commands, DELETE requests, MCP tools marked destructive | **ask** |

MCP tools' own `readOnlyHint` and `destructiveHint` annotations are used when present. Otherwise the risk is guessed from the tool's name, and the guess errs toward asking: `add` (two numbers) is treated like "add a record".

```bash
libra trust allow mcp.files            # everything from one source
libra trust deny api.bank.transfer     # one skill; deny always wins, even over --yes
libra trust allow write                # change a default
libra trust list
libra trust log                        # recent calls: decision, result, arguments (secrets redacted)
libra trust reset mcp.files
```

- **In chat** you're asked before an `ask` skill runs. Answer `y` (yes), `n` (no) or `always` (saves an allow rule).
- **On the command line** you're asked the same way. Pass `--yes` to approve `ask` skills in scripts.
- **On the server** nobody can be asked, so `ask` skills are refused with HTTP 403 and the exact `libra trust allow …` command. `GET /v1/skills?q=words` searches the catalog over HTTP.

Every catalog and plugin call is logged to `~/.libra/audit.jsonl`. Settings live in `~/.libra/`; set `LIBRA_HOME` to use another folder.

## Web scraper and crawler

These also work as standalone commands:

```bash
libra scrape https://example.com                       # Markdown: title, text, tables
libra scrape https://example.com --select "h2" --select "a.button"
libra scrape https://example.com --format json         # everything: links, images, meta, JSON-LD, headings

libra crawl https://example.com --max-pages 50 --depth 2 --out pages.jsonl
libra data pages.jsonl --out datasets/site             # turn a crawl into training data
libra data --crawl https://example.com --crawl-pages 100 --out datasets/site   # or do both at once
```

CSS selectors support tags, `#id`, `.class`, `[attr]`, `[attr=v]`, `[attr^=v]`, `[attr$=v]`, `[attr*=v]`, `[attr~=v]`, `:first-child`, `:last-child`, `:nth-child(n)`, descendant and `>` child combinators, and comma lists.

The crawler and scraper are polite and safe by default:

- They obey robots.txt, including `Crawl-delay`, and wait `--delay` seconds (default 1) between requests to a site.
- They stay on the starting site unless you pass `--any-site`, and skip images, archives and media.
- Pages with identical content are fetched but not saved twice. Downloads are capped at 10 MB.
- They refuse local and LAN addresses (including after redirects) unless you pass `--allow-private`. That stops a served Libra from being used to probe your network.

`--ignore-robots` exists for sites you own. Respect each website's terms, and only use text you have the right to use.

## GPU (optional)

Training and generation run on an NVIDIA GPU through CuPy, which is an array library, not a model. Install the CuPy build for your CUDA version, then pass `--device gpu`:

```bash
pip install cupy-cuda12x        # or: pip install -e .[gpu]
libra train datasets/mine --device gpu --layers 8 --embd 384 --heads 6 --ctx 256 --vocab 8192
libra serve --model checkpoints/large --device gpu
```

`--device auto` (the default) uses the GPU when CuPy finds one, otherwise the CPU. Checkpoints are the same on both, so you can train on a GPU and chat on a laptop.

## Generation speed

Generation keeps a key/value cache, so each new token costs one small step instead of re-reading the whole context. On a 4-core CPU with the default model, that is about **1,500 tokens/s versus 61 without the cache**. When the context window fills, the most recent half is kept and re-encoded, so replies can be longer than `--ctx`.

## Run it as a local API (no key)

```bash
libra serve --model checkpoints/mine --host 0.0.0.0 --port 8000
```

| Endpoint | |
|---|---|
| `GET /v1/models` | lists the model |
| `POST /v1/chat/completions` | OpenAI chat format, `stream: true` supported |
| `POST /v1/completions` | plain text completion |
| `GET /v1/skills` | skills as OpenAI tool schemas; `?q=words` searches the catalog |
| `POST /v1/skills/<name>` | run a skill: `{"url": "…"}` or `{"arguments": "{…}"}` |
| `GET /health` | status check |

Supported request fields: `messages` / `prompt`, `max_tokens`, `temperature`, `top_p`, `top_k`, `repetition_penalty`, `stop`, `seed`, `stream`. Any API key value is accepted, because none is needed.

```bash
curl http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"messages":[{"role":"user","content":"Hi!"}]}'
```

### Using it from Nexus Agent

Nexus Agent can talk to any OpenAI-compatible endpoint. Start `libra serve --host 0.0.0.0` on a PC on the same Wi-Fi, then add an agent with:

- **Base URL:** `http://<your-pc-ip>:8000/v1`
- **Model:** `libra`
- **API key:** leave empty, or type anything

Only use `--host 0.0.0.0` on a network you trust. The server has no authentication, and anyone who can reach it can run its skills, including the web fetchers. Those still refuse LAN addresses unless you started it with `--allow-private`.

## Use it as a library

```python
from libra import BPETokenizer, GPT, GPTConfig, SamplingParams, generate, load

model, tokenizer, meta = load("checkpoints/mine")
print(generate(model, tokenizer, "Once upon a time", SamplingParams(max_tokens=100, temperature=0.8)))
```

Or build and train a model directly:

```python
import numpy as np
from libra.nn import GPT, GPTConfig
from libra.optim import AdamW

model = GPT(GPTConfig(vocab_size=512, block_size=64, n_layer=2, n_head=4, n_embd=96))
opt = AdamW(model.parameters(), lr=3e-3)
x = np.random.randint(0, 512, (8, 64)); y = np.roll(x, -1, axis=1)
_, loss = model(x, y)
model.zero_grad(); loss.backward(); opt.step()
```

## Tests

```bash
python -m unittest discover -s tests -v
```

## How it works (short version)

1. **Tokenizer.** Text is split into bytes. The most frequent adjacent pair is merged into a new token, repeatedly, until the vocabulary is full. Any text can be encoded, because everything is built from bytes.
2. **Model.** Each token becomes a vector and gets a position vector added. Transformer blocks then mix the information: causal self-attention lets each position look at earlier ones, and an MLP transforms each position. A final projection (sharing weights with the input embedding) scores every possible next token.
3. **Training.** Cross-entropy loss measures how surprised the model is by the real next token. The autograd engine computes the gradient of that loss for every weight, and AdamW updates the weights.
4. **Generation.** The model scores the next token, one is sampled, it is appended, and the process repeats.

## License

MIT
