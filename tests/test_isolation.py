"""Serving and training at the same time; checkpoints published as a unit; honest validation reporting."""
import json
import os
import tempfile
import threading
import unittest

import numpy as np


class GradientIsolationTest(unittest.TestCase):
    def test_generation_in_one_thread_does_not_disable_training_in_another(self):
        from libra import autograd as ag
        from libra.generate import SamplingParams, stream_ids
        from libra.nn import GPT, GPTConfig

        ag.seed(0)
        model = GPT(GPTConfig(30, 16, 1, 2, 16, 0.0))
        served = GPT(GPTConfig(30, 16, 1, 2, 16, 0.0)).eval()
        paused, resume = threading.Event(), threading.Event()

        def serve():  # a streaming reply that is paused between two tokens, like a slow HTTP client
            gen = stream_ids(served, [1, 2, 3], SamplingParams(max_tokens=5, temperature=0))
            next(gen)
            paused.set()
            resume.wait(10)
            list(gen)

        t = threading.Thread(target=serve)
        t.start()
        self.assertTrue(paused.wait(10))
        try:
            x = np.random.default_rng(0).integers(0, 30, (2, 8))
            _, loss = model.train()(x, np.roll(x, -1, axis=1))
            loss.backward()
            missing = [n for n, p in model.named_parameters() if p.grad is None]
            self.assertEqual(missing, [], "training lost its gradients while a reply was being generated")
        finally:
            resume.set()
            t.join()

    def test_no_grad_is_per_thread(self):
        from libra import autograd as ag

        inside, other = threading.Event(), {}
        with ag.no_grad():
            t = threading.Thread(target=lambda: other.update(enabled=ag.grad_enabled()))
            t.start()
            t.join()
            self.assertFalse(ag.grad_enabled())
        self.assertTrue(other["enabled"])
        self.assertTrue(ag.grad_enabled())


if __name__ == "__main__":
    unittest.main()


class CheckpointTest(unittest.TestCase):
    def setUp(self):
        from libra import autograd as ag
        from libra.nn import GPT, GPTConfig
        from libra.tokenizer import BPETokenizer

        ag.seed(0)
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self.tmp.name, "ckpt")
        self.tok = BPETokenizer.train("hello world " * 20, 280)
        self.make = lambda layers: GPT(GPTConfig(self.tok.vocab_size, 16, layers, 2, 16, 0.0))

    def tearDown(self):
        self.tmp.cleanup()

    def test_published_as_a_unit(self):
        from libra import checkpoint

        v1 = checkpoint.save(self.dir, self.make(1), self.tok, {"step": 1})
        self.assertEqual(checkpoint.version(self.dir), v1)
        # a save that dies half-way (after the weights, before the tokenizer) must not change what readers see
        real = self.tok.save
        self.tok.save = lambda path: (_ for _ in ()).throw(OSError("disk full"))
        with self.assertRaises(OSError):
            checkpoint.save(self.dir, self.make(2), self.tok, {"step": 2})
        self.tok.save = real
        self.assertEqual(checkpoint.version(self.dir), v1)
        model, _, meta = checkpoint.load(self.dir)
        self.assertEqual((model.config.n_layer, meta["step"]), (1, 1))
        self.assertEqual([n for n in os.listdir(os.path.join(self.dir, "versions")) if n.endswith(".partial")], [])
        v3 = checkpoint.save(self.dir, self.make(2), self.tok, {"step": 3})
        self.assertNotEqual(v3, v1)
        self.assertEqual(checkpoint.load(self.dir)[0].config.n_layer, 2)
        for i in range(5):
            checkpoint.save(self.dir, self.make(1), self.tok, {"step": 10 + i})
        self.assertEqual(len(os.listdir(os.path.join(self.dir, "versions"))), checkpoint.KEEP)  # old ones pruned

    def test_readers_never_see_a_mix(self):
        from libra import checkpoint

        checkpoint.save(self.dir, self.make(1), self.tok, {"step": 0})
        stop, problems = threading.Event(), []

        def reader():
            while not stop.is_set():
                try:
                    model, tok, meta = checkpoint.load(self.dir)
                    if model.config.n_layer != meta["layers"]:
                        problems.append("weights and config from different saves")
                except FileNotFoundError:
                    pass  # pruned between resolving and reading (only possible KEEP versions behind)

        checkpoint.save(self.dir, self.make(1), self.tok, {"step": 0, "layers": 1})
        t = threading.Thread(target=reader)
        t.start()
        for i in range(12):
            layers = 1 + i % 2
            checkpoint.save(self.dir, self.make(layers), self.tok, {"step": i, "layers": layers})
        stop.set()
        t.join()
        self.assertEqual(problems, [])

    def test_old_flat_checkpoints_still_load(self):
        from libra import checkpoint

        os.makedirs(self.dir)
        model = self.make(1)
        with open(os.path.join(self.dir, "config.json"), "w") as f:
            json.dump({"model": model.config.to_dict(), "meta": {"step": 7}}, f)
        np.savez(os.path.join(self.dir, "model.npz"), **model.state_dict())
        self.tok.save(os.path.join(self.dir, "tokenizer.json"))
        self.assertTrue(checkpoint.exists(self.dir))
        self.assertEqual(checkpoint.load(self.dir)[2]["step"], 7)
        checkpoint.save(self.dir, model, self.tok, {"step": 8})  # upgrades to the versioned layout
        self.assertEqual(checkpoint.read_meta(self.dir)["step"], 8)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "model.npz")))


class ValidationReportingTest(unittest.TestCase):
    def test_no_holdout_reports_training_loss(self):
        from libra import checkpoint
        from libra.train import TrainConfig, train

        with tempfile.TemporaryDirectory() as d:
            data = os.path.join(d, "tiny.txt")
            with open(data, "w") as f:
                f.write("the cat sat on the mat. " * 12)  # too little for a held-out split
            out = os.path.join(d, "ckpt")
            lines = []
            train(TrainConfig(data=[data], out=out, vocab_size=300, block_size=16, n_layer=1, n_head=2, n_embd=16,
                              batch_size=4, steps=4, eval_every=2, log_every=100, device="cpu"), log=lines.append)
            meta = checkpoint.read_meta(out)
            self.assertIsNone(meta["val_loss"])
            self.assertIn("train_loss", meta)
            self.assertFalse(os.path.exists(os.path.join(out, "best")))  # no "best" chosen by training loss
            self.assertTrue(any("train loss" in l and "no held-out split" in l for l in lines))
            self.assertFalse(any("val loss" in l for l in lines))

            with open(data, "w") as f:
                f.write(" ".join(f"word{i % 97} is number {i}." for i in range(600)))  # enough text now
            out2 = os.path.join(d, "ckpt2")
            train(TrainConfig(data=[data], out=out2, vocab_size=300, block_size=16, n_layer=1, n_head=2, n_embd=16,
                              batch_size=4, steps=4, eval_every=2, log_every=100, device="cpu"), log=lambda *a: None)
            self.assertIsNotNone(checkpoint.read_meta(out2)["val_loss"])
            self.assertTrue(checkpoint.exists(os.path.join(out2, "best")))
