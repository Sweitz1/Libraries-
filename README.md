# Libra: a language model written from scratch

Libra is a small GPT-style language model where **every piece is original code**. It uses no pretrained weights, no downloaded model, no API key and no subscription. You train it on your own text, and it runs on your own computer.

The only dependency is **NumPy**. Everything else is in this repo:

| File | What it is |
|---|---|
| `libra/autograd.py` | Reverse-mode automatic differentiation engine (tensors, broadcasting, matmul, softmax, layer norm, GELU, cross-entropy, embeddings, dropout) |
| `libra/nn.py` | Modules and the transformer: causal multi-head self-attention, MLP, pre-norm residual blocks, tied embeddings |
| `libra/tokenizer.py` | Byte-level BPE tokenizer trained on your corpus, with chat special tokens |
| `libra/optim.py` | AdamW, gradient clipping, warmup + cosine learning-rate schedule |
| `libra/train.py` | Data loading (`.txt`, `.md`, `.jsonl` chats), training loop, validation, checkpoints, resume |
| `libra/generate.py` | Sampling: temperature, top-k, top-p, repetition penalty, streaming UTF-8 decode |
| `libra/server.py` | OpenAI-compatible HTTP API (standard library only), with streaming |
| `libra/cli.py` | `libra train / generate / chat / serve / info` |

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

A rule of thumb: you want at least about 20 training tokens per parameter. Keep an eye on the logged `val loss`. If it rises while the training loss keeps falling, the model is memorising: add text or use a smaller model.

**What to expect:** a model this size learns the style, vocabulary and common phrases of its data. It does not know facts about the world that you don't teach it. Quality grows with the amount of text and training time.

### Useful flags

`--vocab` tokenizer size · `--ctx` context length · `--layers` `--heads` `--embd` model size · `--batch` batch size · `--steps` training steps · `--lr` peak learning rate · `--dropout` · `--eval-every` · `--resume` · `--sample-prompt` text used for the preview printed at each evaluation.

## Run it as a local API (no key)

```bash
libra serve --model checkpoints/mine --host 0.0.0.0 --port 8000
```

| Endpoint | |
|---|---|
| `GET /v1/models` | lists the model |
| `POST /v1/chat/completions` | OpenAI chat format, `stream: true` supported |
| `POST /v1/completions` | plain text completion |
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

Only use `--host 0.0.0.0` on a network you trust. The server has no authentication.

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
