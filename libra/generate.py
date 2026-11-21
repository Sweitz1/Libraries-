"""Sampling text from a trained model."""
from __future__ import annotations

import codecs
from dataclasses import dataclass

import numpy as np

from .autograd import no_grad
from .nn import GPT
from .tokenizer import EOT, BPETokenizer


@dataclass
class SamplingParams:
    max_tokens: int = 200
    temperature: float = 0.8
    top_k: int = 0
    top_p: float = 0.95
    repetition_penalty: float = 1.0
    seed: int | None = None


def sample_token(logits: np.ndarray, params: SamplingParams, rng: np.random.Generator, history=()) -> int:
    logits = logits.astype(np.float64).copy()
    if params.repetition_penalty != 1.0 and len(history):
        seen = np.unique(np.asarray(history[-64:]))
        vals = logits[seen]
        logits[seen] = np.where(vals > 0, vals / params.repetition_penalty, vals * params.repetition_penalty)
    if params.temperature <= 0:
        return int(np.argmax(logits))
    logits /= params.temperature
    if params.top_k and 0 < params.top_k < len(logits):
        kth = np.partition(logits, -params.top_k)[-params.top_k]
        logits[logits < kth] = -np.inf
    probs = np.exp(logits - logits.max())
    probs /= probs.sum()
    if 0 < params.top_p < 1:
        order = np.argsort(-probs)
        cumulative = np.cumsum(probs[order])
        cutoff = int(np.searchsorted(cumulative, params.top_p)) + 1
        keep = np.zeros_like(probs, dtype=bool)
        keep[order[:cutoff]] = True
        probs = np.where(keep, probs, 0.0)
        probs /= probs.sum()
    return int(rng.choice(len(probs), p=probs))


def stream_ids(model: GPT, prompt_ids: list[int], params: SamplingParams, stop_ids=()):
    """Yield new token ids one at a time. Stops at ``stop_ids`` or ``max_tokens``."""
    if not prompt_ids:
        raise ValueError("prompt_ids must not be empty")
    rng = np.random.default_rng(params.seed)
    ids = list(prompt_ids)
    stop = set(stop_ids)
    block = model.config.block_size
    model.eval()
    with no_grad():
        for _ in range(params.max_tokens):
            context = ids[-block:]
            logits, _ = model(np.array([context], dtype=np.int64))
            nxt = sample_token(logits.data[0, -1], params, rng, ids)
            if nxt in stop:
                return
            ids.append(nxt)
            yield nxt


def stream_text(model: GPT, tokenizer: BPETokenizer, prompt: str, params: SamplingParams, stop_tokens=()):
    """Yield decoded text pieces as they are generated (UTF-8 safe)."""
    stop_ids = tuple(tokenizer.token_id(t) for t in stop_tokens)
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    prompt_ids = tokenizer.encode(prompt) or [tokenizer.token_id(EOT)]
    for tid in stream_ids(model, prompt_ids, params, stop_ids):
        piece = decoder.decode(tokenizer.decode_bytes([tid], skip_special=True))
        if piece:
            yield piece
    tail = decoder.decode(b"", final=True)
    if tail:
        yield tail


def generate(model: GPT, tokenizer: BPETokenizer, prompt: str, params: SamplingParams | None = None, stop_tokens=()) -> str:
    return "".join(stream_text(model, tokenizer, prompt, params or SamplingParams(), stop_tokens))
