"""Training loop: tokenize your text, train the transformer, save checkpoints."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field

import numpy as np

from . import autograd as ag
from . import backend as B
from . import checkpoint
from .autograd import no_grad
from .chat import render_document
from .generate import SamplingParams, generate
from .nn import GPT, GPTConfig
from .optim import AdamW, clip_grad_norm, cosine_lr
from .tokenizer import EOT, BPETokenizer

TEXT_EXTENSIONS = {".txt", ".md", ".jsonl"}


@dataclass
class TrainConfig:
    data: list[str] = field(default_factory=list)
    out: str = "checkpoints/model"
    vocab_size: int = 1024
    block_size: int = 128
    n_layer: int = 4
    n_head: int = 4
    n_embd: int = 128
    dropout: float = 0.1
    batch_size: int = 16
    steps: int = 2000
    lr: float = 3e-3
    warmup: int = 100
    weight_decay: float = 0.1
    grad_clip: float = 1.0
    eval_every: int = 200
    eval_batches: int = 10
    log_every: int = 20
    val_fraction: float = 0.1
    seed: int = 1337
    resume: bool = False
    sample_prompt: str = ""
    device: str = "auto"
    max_seconds: float = 0  # stop (and save) after this long; 0 = no limit


def _iter_files(paths: list[str]):
    for path in paths:
        if os.path.isdir(path):
            for root, _, files in os.walk(path):
                for name in sorted(files):
                    if os.path.splitext(name)[1].lower() in TEXT_EXTENSIONS:
                        yield os.path.join(root, name)
        elif os.path.exists(path):
            yield path
        else:
            raise FileNotFoundError(path)


def load_corpus(paths: list[str]) -> str:
    """Read .txt/.md files as plain text and .jsonl as chats (``messages``) or ``text`` records."""
    docs = []
    for path in _iter_files(paths):
        with open(path, encoding="utf-8", errors="replace") as f:
            if path.lower().endswith(".jsonl"):
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        docs.append(render_document(json.loads(line)))
                    except json.JSONDecodeError as exc:
                        raise ValueError(f"{path}:{line_no}: invalid JSON ({exc})") from exc
            else:
                docs.append(f.read().strip() + EOT)
    if not docs:
        raise ValueError("no training text found")
    return "\n".join(docs)


def get_batch(data: np.ndarray, block: int, batch: int, rng: np.random.Generator):
    starts = rng.integers(0, len(data) - block - 1, size=batch)
    x = np.stack([data[s : s + block] for s in starts]).astype(np.int64)
    y = np.stack([data[s + 1 : s + block + 1] for s in starts]).astype(np.int64)
    return x, y


def estimate_loss(model: GPT, data: np.ndarray, cfg: TrainConfig, rng: np.random.Generator) -> float:
    model.eval()
    losses = []
    with no_grad():
        for _ in range(cfg.eval_batches):
            x, y = get_batch(data, model.config.block_size, cfg.batch_size, rng)
            _, loss = model(x, y)
            losses.append(loss.item())
    model.train()
    return float(np.mean(losses))


def train(cfg: TrainConfig, log=print, progress=None) -> GPT:
    """Train per ``cfg``. ``progress(step, total_steps, loss)`` is called at each log interval if given."""
    log(f"device: {B.use(cfg.device)}")
    ag.seed(cfg.seed)
    rng = np.random.default_rng(cfg.seed)
    text = load_corpus(cfg.data)
    log(f"corpus: {len(text):,} characters")

    tok_path = os.path.join(cfg.out, "tokenizer.json")
    resuming = cfg.resume and os.path.exists(os.path.join(cfg.out, "model.npz"))
    if resuming:
        model, tokenizer, meta = checkpoint.load(cfg.out)
        start_step = int(meta.get("step", 0))
        best_val = float(meta.get("best_val", float("inf")))
        log(f"resuming from {cfg.out} at step {start_step}")
    else:
        t0 = time.time()
        tokenizer = BPETokenizer.train(text, cfg.vocab_size, verbose=True)
        log(f"tokenizer: {tokenizer.vocab_size} tokens ({time.time() - t0:.1f}s)")
        model = None
        start_step, best_val = 0, float("inf")

    ids = np.array(tokenizer.encode(text), dtype=np.int32)
    log(f"tokens: {len(ids):,}")
    block = cfg.block_size if model is None else model.config.block_size
    n_val = int(len(ids) * cfg.val_fraction)
    train_ids, val_ids = ids[: len(ids) - n_val], ids[len(ids) - n_val :]
    if len(train_ids) < block + 2:
        raise ValueError(f"not enough text: {len(train_ids)} tokens for block size {block}; add data or lower --ctx")
    if len(val_ids) < block + 2:
        log("validation split too small; evaluating on training data")
        val_ids = train_ids

    if model is None:
        model = GPT(GPTConfig(tokenizer.vocab_size, block, cfg.n_layer, cfg.n_head, cfg.n_embd, cfg.dropout))
    log(f"model: {model.num_parameters():,} parameters, config {model.config.to_dict()}")

    opt = AdamW(model.parameters(), lr=cfg.lr, weight_decay=cfg.weight_decay)
    if resuming:
        state = checkpoint.load_optimizer_state(cfg.out)
        if state is not None:
            opt.load_state_dict(state)

    os.makedirs(cfg.out, exist_ok=True)
    tokenizer.save(tok_path)
    model.train()
    t0, tokens_seen = time.time(), 0
    step = start_step
    for step in range(start_step, cfg.steps):
        lr = cosine_lr(step, cfg.lr, cfg.warmup, cfg.steps)
        x, y = get_batch(train_ids, block, cfg.batch_size, rng)
        _, loss = model(x, y)
        model.zero_grad()
        loss.backward()
        norm = clip_grad_norm(model.parameters(), cfg.grad_clip)
        opt.step(lr)
        tokens_seen += x.size

        if step % cfg.log_every == 0 or step == cfg.steps - 1:
            rate = tokens_seen / max(1e-9, time.time() - t0)
            log(f"step {step:5d} | loss {loss.item():.4f} | lr {lr:.2e} | grad {norm:.2f} | {rate:,.0f} tok/s")
            if progress:
                progress(step + 1, cfg.steps, loss.item())

        out_of_time = bool(cfg.max_seconds) and time.time() - t0 > cfg.max_seconds
        last = step == cfg.steps - 1 or out_of_time
        if (cfg.eval_every and step > 0 and step % cfg.eval_every == 0) or last:
            val = estimate_loss(model, val_ids, cfg, rng)
            meta = {"step": step + 1, "val_loss": val, "best_val": min(best_val, val)}
            checkpoint.save(cfg.out, model, tokenizer, meta, opt)
            if val < best_val:
                best_val = val
                checkpoint.save(os.path.join(cfg.out, "best"), model, tokenizer, meta)
            log(f"eval  {step:5d} | val loss {val:.4f} | best {best_val:.4f} | saved {cfg.out}")
            sample = generate(model, tokenizer, cfg.sample_prompt,
                              SamplingParams(max_tokens=60, temperature=0.8, seed=step))
            log("sample: " + sample.replace("\n", " ")[:200])
            model.train()
        if out_of_time:
            log(f"time limit reached after step {step + 1}; checkpoint saved")
            break
    model.eval()
    return model
