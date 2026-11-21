"""AdamW optimizer, gradient clipping and learning-rate schedule."""
from __future__ import annotations

import math

import numpy as np

from . import backend as B


class AdamW:
    def __init__(self, params, lr: float = 3e-4, betas=(0.9, 0.95), eps: float = 1e-8, weight_decay: float = 0.1):
        self.params = list(params)
        self.lr = lr
        self.beta1, self.beta2 = betas
        self.eps = eps
        self.weight_decay = weight_decay
        self.t = 0
        self.m = [B.xp.zeros_like(p.data) for p in self.params]
        self.v = [B.xp.zeros_like(p.data) for p in self.params]

    def step(self, lr: float | None = None) -> None:
        lr = self.lr if lr is None else lr
        self.t += 1
        c1 = 1 - self.beta1**self.t
        c2 = 1 - self.beta2**self.t
        for p, m, v in zip(self.params, self.m, self.v):
            if p.grad is None:
                continue
            g = p.grad
            m *= self.beta1
            m += (1 - self.beta1) * g
            v *= self.beta2
            v += (1 - self.beta2) * (g * g)
            if self.weight_decay and p.data.ndim >= 2:  # no decay on biases / norms
                p.data -= lr * self.weight_decay * p.data
            p.data -= lr * (m / c1) / (B.xp.sqrt(v / c2) + self.eps)

    def state_dict(self) -> dict[str, np.ndarray]:
        state = {"t": np.array(self.t)}
        for i, (m, v) in enumerate(zip(self.m, self.v)):
            state[f"m{i}"] = B.to_numpy(m)
            state[f"v{i}"] = B.to_numpy(v)
        return state

    def load_state_dict(self, state) -> None:
        self.t = int(state["t"])
        for i in range(len(self.params)):
            self.m[i] = B.asarray(np.array(state[f"m{i}"], dtype=np.float32))
            self.v[i] = B.asarray(np.array(state[f"v{i}"], dtype=np.float32))


def clip_grad_norm(params, max_norm: float) -> float:
    grads = [p.grad for p in params if p.grad is not None]
    total = math.sqrt(sum(float((g.astype(np.float64) ** 2).sum()) for g in grads))
    if max_norm > 0 and total > max_norm:
        scale = max_norm / (total + 1e-6)
        for p in params:
            if p.grad is not None:
                p.grad = p.grad * scale
    return total


def cosine_lr(step: int, max_lr: float, warmup: int, total: int, min_ratio: float = 0.1) -> float:
    if step < warmup:
        return max_lr * (step + 1) / warmup
    if step >= total:
        return max_lr * min_ratio
    progress = (step - warmup) / max(1, total - warmup)
    return max_lr * (min_ratio + (1 - min_ratio) * 0.5 * (1 + math.cos(math.pi * progress)))
