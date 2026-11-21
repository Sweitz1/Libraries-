import unittest

import numpy as np

from libra import autograd as ag
from libra.autograd import Tensor


def numeric_grad(f, x: np.ndarray, eps: float = 1e-6) -> np.ndarray:
    g = np.zeros_like(x)
    it = np.nditer(x, flags=["multi_index"])
    for _ in it:
        i = it.multi_index
        old = x[i]
        x[i] = old + eps
        hi = f()
        x[i] = old - eps
        lo = f()
        x[i] = old
        g[i] = (hi - lo) / (2 * eps)
    return g


class GradCheck(unittest.TestCase):
    def check(self, build, *shapes, rtol=1e-4, atol=1e-6):
        rng = np.random.default_rng(0)
        arrays = [rng.normal(size=s) for s in shapes]  # float64 for precise checks
        tensors = [Tensor(a, requires_grad=True) for a in arrays]
        weights = None

        def scalar(out: Tensor):
            nonlocal weights
            if weights is None:
                weights = rng.normal(size=out.shape)
            return (out.data * weights).sum()

        out = build(*tensors)
        scalar(out)
        (out * Tensor(weights)).sum().backward()
        for a, t in zip(arrays, tensors):
            expected = numeric_grad(lambda: scalar(build(*[Tensor(x) for x in arrays])), a)
            np.testing.assert_allclose(t.grad, expected, rtol=rtol, atol=atol)

    def test_add_mul_broadcast(self):
        self.check(lambda a, b: a * b + b, (3, 4), (4,))

    def test_sub_div_pow(self):
        self.check(lambda a, b: (a - b) / (b * b + 2.0) + a**2, (2, 3), (2, 3))

    def test_matmul_batched(self):
        self.check(lambda a, b: a @ b, (2, 3, 4), (4, 5))

    def test_reductions(self):
        self.check(lambda a: a.sum(axis=1) + a.mean(axis=1), (3, 4))
        self.check(lambda a: a.sum(axis=(0, 2), keepdims=True), (2, 3, 4))

    def test_shape_ops(self):
        self.check(lambda a: a.reshape(4, 6).transpose(1, 0)[1:5, ::2], (2, 3, 4))

    def test_elementwise(self):
        self.check(lambda a: a.tanh() + a.exp() + (a * a + 1.0).log(), (3, 3))

    def test_gelu(self):
        self.check(ag.gelu, (4, 5))

    def test_softmax_masked(self):
        mask = np.tril(np.ones((4, 4), dtype=bool))
        self.check(lambda a: ag.softmax(a, axis=-1, mask=mask), (2, 4, 4))

    def test_layer_norm(self):
        self.check(lambda x, w, b: ag.layer_norm(x, w, b), (2, 3, 6), (6,), (6,))

    def test_cross_entropy(self):
        targets = np.array([1, 0, 3])
        rng = np.random.default_rng(1)
        x = rng.normal(size=(3, 5))
        t = Tensor(x, requires_grad=True)
        ag.cross_entropy(t, targets).backward()
        expected = numeric_grad(lambda: ag.cross_entropy(Tensor(x), targets).item(), x)
        np.testing.assert_allclose(t.grad, expected, rtol=1e-4, atol=1e-7)

    def test_embedding_repeated_indices(self):
        idx = np.array([[0, 2, 2], [1, 0, 2]])
        self.check(lambda w: ag.embedding(w, idx), (3, 4))

    def test_no_grad(self):
        a = Tensor(np.ones(3), requires_grad=True)
        with ag.no_grad():
            b = a * 2
        self.assertFalse(b.requires_grad)


if __name__ == "__main__":
    unittest.main()
