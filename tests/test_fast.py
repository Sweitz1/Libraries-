import unittest

import numpy as np

from libra import autograd as ag
from libra import backend as B
from libra.autograd import no_grad
from libra.generate import SamplingParams, stream_ids
from libra.nn import GPT, GPTConfig


class KVCacheTest(unittest.TestCase):
    def setUp(self):
        ag.seed(0)
        self.model = GPT(GPTConfig(50, block_size=16, n_layer=2, n_head=2, n_embd=16, dropout=0.0)).eval()
        self.ids = np.random.default_rng(0).integers(0, 50, (2, 12))

    def test_cached_matches_full_forward(self):
        with no_grad():
            full = self.model(self.ids)[0].data
            cache = self.model.new_cache()
            parts = [self.model.infer(self.ids[:, :5], cache)]  # prefill
            for t in range(5, 12):  # then one token at a time
                parts.append(self.model.infer(self.ids[:, t : t + 1], cache))
        np.testing.assert_allclose(np.concatenate(parts, axis=1), full, rtol=1e-4, atol=1e-5)
        self.assertEqual(cache.length, 12)

    def test_cache_overflow_raises(self):
        cache = self.model.new_cache()
        with no_grad():
            self.model.infer(self.ids[:, :12], cache)
            with self.assertRaises(ValueError):
                self.model.infer(self.ids[:, :5], cache)

    def test_generation_past_block_size(self):
        p = SamplingParams(max_tokens=40, temperature=1.0, top_p=1.0, seed=3)
        out = list(stream_ids(self.model, [1, 2, 3], p))
        self.assertEqual(len(out), 40)  # sliding window keeps going past block_size=16

    def test_greedy_matches_uncached_reference(self):
        p = SamplingParams(max_tokens=10, temperature=0)
        fast = list(stream_ids(self.model, [4, 5, 6], p))
        ids = [4, 5, 6]
        with no_grad():
            for _ in range(10):
                ids.append(int(np.argmax(self.model(np.array([ids]))[0].data[0, -1])))
        self.assertEqual(fast, ids[3:])


class BackendTest(unittest.TestCase):
    def tearDown(self):
        B.use("cpu")

    def test_cpu_and_auto(self):
        self.assertEqual(B.use("cpu"), "cpu")
        self.assertIn(B.use("auto"), ("cpu", "gpu"))

    def test_gpu_request_without_gpu(self):
        if B.gpu_available():
            self.skipTest("GPU present")
        with self.assertRaises(RuntimeError):
            B.use("gpu")

    def test_bad_device(self):
        with self.assertRaises(ValueError):
            B.use("tpu")


if __name__ == "__main__":
    unittest.main()
