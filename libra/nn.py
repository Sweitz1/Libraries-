"""Neural network modules and the GPT-style transformer, written from scratch."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from . import autograd as ag
from . import backend as B
from .autograd import Tensor


class Parameter(Tensor):
    def __init__(self, data):
        super().__init__(B.asarray(np.asarray(data, dtype=np.float32)), requires_grad=True)


class Module:
    training = True

    def __call__(self, *args, **kwargs):
        return self.forward(*args, **kwargs)

    def forward(self, *args, **kwargs):  # pragma: no cover - abstract
        raise NotImplementedError

    def _children(self):
        for name, value in vars(self).items():
            if isinstance(value, (Module, Parameter)):
                yield name, value
            elif isinstance(value, (list, tuple)):
                for i, item in enumerate(value):
                    if isinstance(item, (Module, Parameter)):
                        yield f"{name}.{i}", item

    def named_parameters(self, prefix: str = ""):
        for name, child in self._children():
            if isinstance(child, Parameter):
                yield prefix + name, child
            else:
                yield from child.named_parameters(prefix + name + ".")

    def parameters(self) -> list[Parameter]:
        return [p for _, p in self.named_parameters()]

    def modules(self):
        yield self
        for _, child in self._children():
            if isinstance(child, Module):
                yield from child.modules()

    def train(self, mode: bool = True) -> "Module":
        for m in self.modules():
            m.training = mode
        return self

    def eval(self) -> "Module":
        return self.train(False)

    def zero_grad(self) -> None:
        for p in self.parameters():
            p.grad = None

    def num_parameters(self) -> int:
        return sum(p.data.size for p in self.parameters())

    def state_dict(self) -> dict[str, np.ndarray]:
        return {name: B.to_numpy(p.data).copy() for name, p in self.named_parameters()}

    def load_state_dict(self, state: dict[str, np.ndarray]) -> None:
        params = dict(self.named_parameters())
        missing = set(params) - set(state)
        unexpected = set(state) - set(params)
        if missing or unexpected:
            raise ValueError(f"state mismatch: missing={sorted(missing)} unexpected={sorted(unexpected)}")
        for name, p in params.items():
            if state[name].shape != p.data.shape:
                raise ValueError(f"shape mismatch for {name}: {state[name].shape} vs {p.data.shape}")
            p.data = B.asarray(np.array(state[name], dtype=np.float32))


class Linear(Module):
    def __init__(self, n_in: int, n_out: int, bias: bool = True, std: float = 0.02):
        self.weight = Parameter(ag.rng().normal(0.0, std, (n_in, n_out)))
        self.bias = Parameter(np.zeros(n_out)) if bias else None

    def forward(self, x: Tensor) -> Tensor:
        y = x @ self.weight
        return y + self.bias if self.bias is not None else y


class Embedding(Module):
    def __init__(self, n: int, dim: int, std: float = 0.02):
        self.weight = Parameter(ag.rng().normal(0.0, std, (n, dim)))

    def forward(self, idx) -> Tensor:
        return ag.embedding(self.weight, idx)


class LayerNorm(Module):
    def __init__(self, dim: int):
        self.weight = Parameter(np.ones(dim))
        self.bias = Parameter(np.zeros(dim))

    def forward(self, x: Tensor) -> Tensor:
        return ag.layer_norm(x, self.weight, self.bias)


@dataclass
class GPTConfig:
    vocab_size: int
    block_size: int = 128
    n_layer: int = 4
    n_head: int = 4
    n_embd: int = 128
    dropout: float = 0.1

    def to_dict(self) -> dict:
        return asdict(self)


class CausalSelfAttention(Module):
    def __init__(self, cfg: GPTConfig):
        if cfg.n_embd % cfg.n_head:
            raise ValueError("n_embd must be divisible by n_head")
        self.n_head = cfg.n_head
        self.dropout = cfg.dropout
        self.c_attn = Linear(cfg.n_embd, 3 * cfg.n_embd)
        self.c_proj = Linear(cfg.n_embd, cfg.n_embd, std=0.02 / math.sqrt(2 * cfg.n_layer))
        self._masks: dict = {}

    def _mask(self, t: int):
        key = (t, B.name())
        if key not in self._masks:
            self._masks[key] = B.xp.tril(B.xp.ones((t, t), dtype=bool))
        return self._masks[key]

    def infer(self, x, cache: dict, past: int):
        """Inference-only attention over cached keys/values. ``x`` is a raw array (B, t, C)."""
        xp = B.xp
        b, t, c = x.shape
        h, hd = self.n_head, c // self.n_head
        qkv = x @ self.c_attn.weight.data + self.c_attn.bias.data
        q, k, v = (qkv[:, :, i * c : (i + 1) * c].reshape(b, t, h, hd).transpose(0, 2, 1, 3) for i in range(3))
        if past:
            k = xp.concatenate([cache["k"], k], axis=2)
            v = xp.concatenate([cache["v"], v], axis=2)
        cache["k"], cache["v"] = k, v
        att = (q @ k.transpose(0, 1, 3, 2)) * (1.0 / math.sqrt(hd))
        if t > 1:  # new token i (absolute position past+i) may see keys 0..past+i
            mask = xp.arange(past + t)[None, :] <= (past + xp.arange(t))[:, None]
            att = xp.where(mask, att, -np.inf)
        att = xp.exp(att - att.max(axis=-1, keepdims=True))
        att /= att.sum(axis=-1, keepdims=True)
        y = (att @ v).transpose(0, 2, 1, 3).reshape(b, t, c)
        return y @ self.c_proj.weight.data + self.c_proj.bias.data

    def forward(self, x: Tensor) -> Tensor:
        b, t, c = x.shape
        h, hd = self.n_head, c // self.n_head
        qkv = self.c_attn(x)

        def heads(part: Tensor) -> Tensor:
            return part.reshape(b, t, h, hd).transpose(0, 2, 1, 3)

        q = heads(qkv[:, :, :c])
        k = heads(qkv[:, :, c : 2 * c])
        v = heads(qkv[:, :, 2 * c :])
        att = (q @ k.transpose(0, 1, 3, 2)) * (1.0 / math.sqrt(hd))
        att = ag.softmax(att, axis=-1, mask=self._mask(t))
        att = ag.dropout(att, self.dropout, self.training)
        y = (att @ v).transpose(0, 2, 1, 3).reshape(b, t, c)
        return ag.dropout(self.c_proj(y), self.dropout, self.training)


class MLP(Module):
    def __init__(self, cfg: GPTConfig):
        self.dropout = cfg.dropout
        self.c_fc = Linear(cfg.n_embd, 4 * cfg.n_embd)
        self.c_proj = Linear(4 * cfg.n_embd, cfg.n_embd, std=0.02 / math.sqrt(2 * cfg.n_layer))

    def forward(self, x: Tensor) -> Tensor:
        return ag.dropout(self.c_proj(ag.gelu(self.c_fc(x))), self.dropout, self.training)

    def infer(self, x):
        hidden = ag.gelu(Tensor(x @ self.c_fc.weight.data + self.c_fc.bias.data)).data
        return hidden @ self.c_proj.weight.data + self.c_proj.bias.data


class Block(Module):
    def __init__(self, cfg: GPTConfig):
        self.ln_1 = LayerNorm(cfg.n_embd)
        self.attn = CausalSelfAttention(cfg)
        self.ln_2 = LayerNorm(cfg.n_embd)
        self.mlp = MLP(cfg)

    def forward(self, x: Tensor) -> Tensor:
        x = x + self.attn(self.ln_1(x))
        return x + self.mlp(self.ln_2(x))

    def infer(self, x, cache: dict, past: int):
        x = x + self.attn.infer(_ln(x, self.ln_1), cache, past)
        return x + self.mlp.infer(_ln(x, self.ln_2))


class GPT(Module):
    """Decoder-only transformer language model with tied input/output embeddings."""

    def __init__(self, cfg: GPTConfig):
        self.config = cfg
        self.wte = Embedding(cfg.vocab_size, cfg.n_embd)
        self.wpe = Embedding(cfg.block_size, cfg.n_embd)
        self.blocks = [Block(cfg) for _ in range(cfg.n_layer)]
        self.ln_f = LayerNorm(cfg.n_embd)

    def forward(self, idx, targets=None):
        idx = B.asarray(idx)
        b, t = idx.shape
        if t > self.config.block_size:
            raise ValueError(f"sequence length {t} exceeds block size {self.config.block_size}")
        x = self.wte(idx) + self.wpe(B.xp.arange(t))
        x = ag.dropout(x, self.config.dropout, self.training)
        for block in self.blocks:
            x = block(x)
        x = self.ln_f(x)
        logits = x @ self.wte.weight.T
        loss = None
        if targets is not None:
            loss = ag.cross_entropy(logits.reshape(b * t, -1), B.asarray(targets).reshape(-1))
        return logits, loss

    def new_cache(self) -> "KVCache":
        return KVCache(self)

    def infer(self, idx, cache: "KVCache"):
        """Fast inference with a key/value cache: feed only the new tokens.

        Returns raw logits (B, t, V) for the new positions. Raises if the cache
        would exceed ``block_size``; call ``cache.reset()`` and re-feed a window.
        """
        idx = B.asarray(idx)
        b, t = idx.shape
        past = cache.length
        if past + t > self.config.block_size:
            raise ValueError("KV cache full")
        x = self.wte.weight.data[idx] + self.wpe.weight.data[past : past + t]
        for block, layer_cache in zip(self.blocks, cache.layers):
            x = block.infer(x, layer_cache, past)
        x = _ln(x, self.ln_f)
        cache.length = past + t
        return x @ self.wte.weight.data.T


class KVCache:
    def __init__(self, model: GPT):
        self.layers = [{} for _ in model.blocks]
        self.length = 0

    def reset(self) -> None:
        for layer in self.layers:
            layer.clear()
        self.length = 0


def _ln(x, ln: LayerNorm, eps: float = 1e-5):
    mu = x.mean(axis=-1, keepdims=True)
    var = x.var(axis=-1, keepdims=True)
    return (x - mu) / B.xp.sqrt(var + eps) * ln.weight.data + ln.bias.data
