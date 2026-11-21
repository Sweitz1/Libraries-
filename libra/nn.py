"""Neural network modules and the GPT-style transformer, written from scratch."""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from . import autograd as ag
from .autograd import Tensor


class Parameter(Tensor):
    def __init__(self, data):
        super().__init__(np.asarray(data, dtype=np.float32), requires_grad=True)


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
        return {name: p.data.copy() for name, p in self.named_parameters()}

    def load_state_dict(self, state: dict[str, np.ndarray]) -> None:
        params = dict(self.named_parameters())
        missing = set(params) - set(state)
        unexpected = set(state) - set(params)
        if missing or unexpected:
            raise ValueError(f"state mismatch: missing={sorted(missing)} unexpected={sorted(unexpected)}")
        for name, p in params.items():
            if state[name].shape != p.data.shape:
                raise ValueError(f"shape mismatch for {name}: {state[name].shape} vs {p.data.shape}")
            p.data = np.array(state[name], dtype=np.float32)


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

    def forward(self, idx: np.ndarray) -> Tensor:
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
        self._masks: dict[int, np.ndarray] = {}

    def _mask(self, t: int) -> np.ndarray:
        if t not in self._masks:
            self._masks[t] = np.tril(np.ones((t, t), dtype=bool))
        return self._masks[t]

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


class Block(Module):
    def __init__(self, cfg: GPTConfig):
        self.ln_1 = LayerNorm(cfg.n_embd)
        self.attn = CausalSelfAttention(cfg)
        self.ln_2 = LayerNorm(cfg.n_embd)
        self.mlp = MLP(cfg)

    def forward(self, x: Tensor) -> Tensor:
        x = x + self.attn(self.ln_1(x))
        return x + self.mlp(self.ln_2(x))


class GPT(Module):
    """Decoder-only transformer language model with tied input/output embeddings."""

    def __init__(self, cfg: GPTConfig):
        self.config = cfg
        self.wte = Embedding(cfg.vocab_size, cfg.n_embd)
        self.wpe = Embedding(cfg.block_size, cfg.n_embd)
        self.blocks = [Block(cfg) for _ in range(cfg.n_layer)]
        self.ln_f = LayerNorm(cfg.n_embd)

    def forward(self, idx: np.ndarray, targets: np.ndarray | None = None):
        idx = np.asarray(idx)
        b, t = idx.shape
        if t > self.config.block_size:
            raise ValueError(f"sequence length {t} exceeds block size {self.config.block_size}")
        x = self.wte(idx) + self.wpe(np.arange(t))
        x = ag.dropout(x, self.config.dropout, self.training)
        for block in self.blocks:
            x = block(x)
        x = self.ln_f(x)
        logits = x @ self.wte.weight.T
        loss = None
        if targets is not None:
            loss = ag.cross_entropy(logits.reshape(b * t, -1), np.asarray(targets).reshape(-1))
        return logits, loss
