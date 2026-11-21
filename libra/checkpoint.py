"""Save and load a model directory: config.json, model.npz, tokenizer.json."""
from __future__ import annotations

import json
import os

import numpy as np

from .nn import GPT, GPTConfig
from .tokenizer import BPETokenizer


def save(directory: str, model: GPT, tokenizer: BPETokenizer, meta: dict | None = None, optimizer=None) -> None:
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, "config.json"), "w", encoding="utf-8") as f:
        json.dump({"model": model.config.to_dict(), "meta": meta or {}}, f, indent=2)
    _savez(os.path.join(directory, "model.npz"), model.state_dict())
    tokenizer.save(os.path.join(directory, "tokenizer.json"))
    if optimizer is not None:
        _savez(os.path.join(directory, "optimizer.npz"), optimizer.state_dict())


def _savez(path: str, arrays: dict) -> None:
    tmp = path + ".tmp.npz"
    np.savez(tmp, **arrays)
    os.replace(tmp, path)


def load(directory: str):
    """Return ``(model, tokenizer, meta)``."""
    with open(os.path.join(directory, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    model = GPT(GPTConfig(**cfg["model"]))
    with np.load(os.path.join(directory, "model.npz")) as data:
        model.load_state_dict({k: data[k] for k in data.files})
    tokenizer = BPETokenizer.load(os.path.join(directory, "tokenizer.json"))
    model.eval()
    return model, tokenizer, cfg.get("meta", {})


def load_optimizer_state(directory: str):
    path = os.path.join(directory, "optimizer.npz")
    if not os.path.exists(path):
        return None
    with np.load(path) as data:
        return {k: data[k] for k in data.files}
