"""A small reverse-mode automatic differentiation engine built on NumPy.

Every operation records how to push gradients back to its inputs. Calling
``loss.backward()`` walks the recorded graph in reverse topological order.
"""
from __future__ import annotations

import contextlib
import math
import threading

import numpy as np

from . import backend as B

_state = threading.local()  # gradient recording is on or off per thread, never process-wide
_rng = np.random.default_rng()


def grad_enabled() -> bool:
    return getattr(_state, "enabled", True)


def seed(value: int) -> None:
    """Seed the generators used for dropout and parameter initialisation."""
    global _rng
    _rng = np.random.default_rng(value)
    if B.on_gpu():
        B.xp.random.seed(value)


def rng() -> np.random.Generator:
    return _rng


@contextlib.contextmanager
def no_grad():
    """Disable graph recording (inference, evaluation) in the current thread only.

    Other threads, e.g. a training run while the server answers requests, are unaffected."""
    prev = grad_enabled()
    _state.enabled = False
    try:
        yield
    finally:
        _state.enabled = prev


def _as_array(x):
    """Return ``x`` as a floating-point array on the active device."""
    if isinstance(x, B.xp.ndarray) and np.issubdtype(x.dtype, np.floating):
        return x
    arr = B.to_numpy(x) if not isinstance(x, (int, float, list, tuple)) else np.asarray(x)
    if not np.issubdtype(arr.dtype, np.floating):
        arr = arr.astype(np.float32)
    return B.asarray(arr)


def _unbroadcast(grad: np.ndarray, shape: tuple) -> np.ndarray:
    """Sum ``grad`` down to ``shape`` (the reverse of NumPy broadcasting)."""
    while grad.ndim > len(shape):
        grad = grad.sum(axis=0)
    for i, size in enumerate(shape):
        if size == 1 and grad.shape[i] != 1:
            grad = grad.sum(axis=i, keepdims=True)
    return grad


def _is_basic_index(idx) -> bool:
    items = idx if isinstance(idx, tuple) else (idx,)
    return all(isinstance(i, (int, slice)) or i is None or i is Ellipsis for i in items)


class Tensor:
    __array_priority__ = 100  # make ``ndarray <op> Tensor`` defer to Tensor

    def __init__(self, data, requires_grad: bool = False):
        self.data = _as_array(data)
        self.requires_grad = requires_grad
        self.grad: np.ndarray | None = None
        self._prev: tuple = ()
        self._backward = None

    # ----------------------------------------------------------------- basics
    @property
    def shape(self) -> tuple:
        return self.data.shape

    @property
    def ndim(self) -> int:
        return self.data.ndim

    @property
    def dtype(self):
        return self.data.dtype

    def item(self) -> float:
        return float(self.data)

    def numpy(self) -> np.ndarray:
        return B.to_numpy(self.data)

    def __repr__(self) -> str:
        return f"Tensor(shape={self.shape}, requires_grad={self.requires_grad})"

    @staticmethod
    def _make(data, parents, backward) -> "Tensor":
        out = Tensor(data)
        if grad_enabled() and any(p.requires_grad for p in parents):
            out.requires_grad = True
            out._prev = tuple(p for p in parents if p.requires_grad)
            out._backward = backward
        return out

    def _acc(self, grad: np.ndarray) -> None:
        if not self.requires_grad:
            return
        grad = grad.astype(self.data.dtype, copy=False)
        self.grad = grad if self.grad is None else self.grad + grad

    def backward(self, grad: np.ndarray | None = None) -> None:
        order, seen = [], set()
        stack = [(self, False)]
        while stack:
            node, done = stack.pop()
            if done:
                order.append(node)
                continue
            if id(node) in seen:
                continue
            seen.add(id(node))
            stack.append((node, True))
            for parent in node._prev:
                if id(parent) not in seen:
                    stack.append((parent, False))
        self.grad = B.xp.ones_like(self.data) if grad is None else _as_array(grad)
        for node in reversed(order):
            if node._backward is not None:
                node._backward()
        # free the graph so intermediate arrays can be collected
        for node in order:
            node._prev = ()
            node._backward = None

    # ------------------------------------------------------------- arithmetic
    def __add__(self, other) -> "Tensor":
        other = _ensure(other)

        def bw():
            self._acc(_unbroadcast(out.grad, self.shape))
            other._acc(_unbroadcast(out.grad, other.shape))

        out = Tensor._make(self.data + other.data, (self, other), bw)
        return out

    __radd__ = __add__

    def __neg__(self) -> "Tensor":
        def bw():
            self._acc(-out.grad)

        out = Tensor._make(-self.data, (self,), bw)
        return out

    def __sub__(self, other) -> "Tensor":
        return self + (-_ensure(other))

    def __rsub__(self, other) -> "Tensor":
        return _ensure(other) + (-self)

    def __mul__(self, other) -> "Tensor":
        other = _ensure(other)

        def bw():
            if self.requires_grad:
                self._acc(_unbroadcast(out.grad * other.data, self.shape))
            if other.requires_grad:
                other._acc(_unbroadcast(out.grad * self.data, other.shape))

        out = Tensor._make(self.data * other.data, (self, other), bw)
        return out

    __rmul__ = __mul__

    def __pow__(self, power: float) -> "Tensor":
        def bw():
            self._acc(out.grad * power * self.data ** (power - 1))

        out = Tensor._make(self.data**power, (self,), bw)
        return out

    def __truediv__(self, other) -> "Tensor":
        if isinstance(other, Tensor):
            return self * other**-1.0
        return self * (1.0 / other)

    def __rtruediv__(self, other) -> "Tensor":
        return _ensure(other) * self**-1.0

    def __matmul__(self, other) -> "Tensor":
        other = _ensure(other)

        def bw():
            g = out.grad
            if self.requires_grad:
                self._acc(_unbroadcast(g @ B.xp.swapaxes(other.data, -1, -2), self.shape))
            if other.requires_grad:
                other._acc(_unbroadcast(B.xp.swapaxes(self.data, -1, -2) @ g, other.shape))

        out = Tensor._make(self.data @ other.data, (self, other), bw)
        return out

    # -------------------------------------------------------------- reductions
    def sum(self, axis=None, keepdims: bool = False) -> "Tensor":
        def bw():
            g = out.grad
            if axis is not None and not keepdims:
                g = B.xp.expand_dims(g, axis)
            self._acc(B.xp.broadcast_to(g, self.shape).copy())

        out = Tensor._make(self.data.sum(axis=axis, keepdims=keepdims), (self,), bw)
        return out

    def mean(self, axis=None, keepdims: bool = False) -> "Tensor":
        if axis is None:
            n = self.data.size
        else:
            axes = axis if isinstance(axis, tuple) else (axis,)
            n = math.prod(self.shape[a] for a in axes)
        return self.sum(axis=axis, keepdims=keepdims) * (1.0 / n)

    # ------------------------------------------------------------------ shape
    def reshape(self, *shape) -> "Tensor":
        if len(shape) == 1 and isinstance(shape[0], (tuple, list)):
            shape = tuple(shape[0])

        def bw():
            self._acc(out.grad.reshape(self.shape))

        out = Tensor._make(self.data.reshape(shape), (self,), bw)
        return out

    def transpose(self, *axes) -> "Tensor":
        if not axes:
            axes = tuple(reversed(range(self.ndim)))
        inverse = tuple(np.argsort(axes))

        def bw():
            self._acc(out.grad.transpose(inverse))

        out = Tensor._make(self.data.transpose(axes), (self,), bw)
        return out

    @property
    def T(self) -> "Tensor":
        return self.transpose()

    def __getitem__(self, idx) -> "Tensor":
        def bw():
            g = B.xp.zeros_like(self.data)
            if _is_basic_index(idx):
                g[idx] += out.grad
            else:
                B.scatter_add(g, idx, out.grad)
            self._acc(g)

        out = Tensor._make(self.data[idx], (self,), bw)
        return out

    # ------------------------------------------------------------ elementwise
    def exp(self) -> "Tensor":
        y = B.xp.exp(self.data)

        def bw():
            self._acc(out.grad * y)

        out = Tensor._make(y, (self,), bw)
        return out

    def log(self) -> "Tensor":
        def bw():
            self._acc(out.grad / self.data)

        out = Tensor._make(B.xp.log(self.data), (self,), bw)
        return out

    def tanh(self) -> "Tensor":
        y = B.xp.tanh(self.data)

        def bw():
            self._acc(out.grad * (1 - y * y))

        out = Tensor._make(y, (self,), bw)
        return out

    def relu(self) -> "Tensor":
        def bw():
            self._acc(out.grad * (self.data > 0))

        out = Tensor._make(B.xp.maximum(self.data, 0), (self,), bw)
        return out


def _ensure(x) -> Tensor:
    return x if isinstance(x, Tensor) else Tensor(x)


# --------------------------------------------------------------------------
# Fused operations used by the transformer. Fusing keeps the graph small and
# makes the backward passes numerically stable.
# --------------------------------------------------------------------------

_GELU_C = math.sqrt(2.0 / math.pi)


def gelu(x: Tensor) -> Tensor:
    """GELU activation (tanh approximation)."""
    xd = x.data
    t = B.xp.tanh(_GELU_C * (xd + 0.044715 * xd**3))

    def bw():
        dinner = _GELU_C * (1 + 3 * 0.044715 * xd**2)
        x._acc(out.grad * (0.5 * (1 + t) + 0.5 * xd * (1 - t * t) * dinner))

    out = Tensor._make(0.5 * xd * (1 + t), (x,), bw)
    return out


def softmax(x: Tensor, axis: int = -1, mask=None) -> Tensor:
    """Softmax along ``axis``. Positions where ``mask`` is False get probability 0."""
    z = x.data if mask is None else B.xp.where(mask, x.data, -np.inf)
    z = z - z.max(axis=axis, keepdims=True)
    e = B.xp.exp(z)
    y = e / e.sum(axis=axis, keepdims=True)

    def bw():
        g = out.grad
        x._acc(y * (g - (g * y).sum(axis=axis, keepdims=True)))

    out = Tensor._make(y, (x,), bw)
    return out


def cross_entropy(logits: Tensor, targets) -> Tensor:
    """Mean cross-entropy of ``logits`` (N, V) against integer ``targets`` (N,)."""
    xp = B.xp
    targets = B.asarray(targets)
    z = logits.data - logits.data.max(axis=1, keepdims=True)
    logp = z - xp.log(xp.exp(z).sum(axis=1, keepdims=True))
    rows = xp.arange(len(targets))
    loss = -logp[rows, targets].mean()

    def bw():
        p = xp.exp(logp)
        p[rows, targets] -= 1
        logits._acc(p * (out.grad / len(targets)))

    out = Tensor._make(loss.astype(logits.dtype), (logits,), bw)
    return out


def layer_norm(x: Tensor, weight: Tensor, bias: Tensor, eps: float = 1e-5) -> Tensor:
    xd = x.data
    mu = xd.mean(axis=-1, keepdims=True)
    rstd = 1.0 / B.xp.sqrt(xd.var(axis=-1, keepdims=True) + eps)
    xhat = (xd - mu) * rstd
    c = xd.shape[-1]

    def bw():
        g = out.grad
        if weight.requires_grad:
            weight._acc((g * xhat).reshape(-1, c).sum(axis=0))
        if bias.requires_grad:
            bias._acc(g.reshape(-1, c).sum(axis=0))
        if x.requires_grad:
            dxhat = g * weight.data
            x._acc(
                rstd
                * (
                    dxhat
                    - dxhat.mean(axis=-1, keepdims=True)
                    - xhat * (dxhat * xhat).mean(axis=-1, keepdims=True)
                )
            )

    out = Tensor._make(xhat * weight.data + bias.data, (x, weight, bias), bw)
    return out


def embedding(weight: Tensor, idx) -> Tensor:
    """Look up rows of ``weight`` for integer indices ``idx``."""
    idx = B.asarray(idx)

    def bw():
        g = B.xp.zeros_like(weight.data)
        B.scatter_add(g, idx.reshape(-1), out.grad.reshape(-1, weight.shape[1]))
        weight._acc(g)

    out = Tensor._make(weight.data[idx], (weight,), bw)
    return out


def dropout(x: Tensor, p: float, training: bool) -> Tensor:
    if not training or p <= 0:
        return x
    noise = _rng.random(x.shape, dtype=np.float32) if not B.on_gpu() else B.xp.random.random(x.shape, dtype=np.float32)
    keep = (noise >= p).astype(x.dtype) / (1.0 - p)
    return x * Tensor(keep)
