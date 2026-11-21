import json
import os
import tempfile
import threading
import unittest
import urllib.request

import numpy as np

from libra import autograd as ag
from libra import chat, checkpoint
from libra.generate import SamplingParams, generate
from libra.nn import GPT, GPTConfig
from libra.optim import AdamW, clip_grad_norm
from libra.server import Engine, make_server
from libra.tokenizer import EOT, BPETokenizer

TEXT = ("the quick brown fox jumps over the lazy dog. " * 20
        + "héllo wörld — ünïcode ✓ 你好 🙂 snake_case 12345 ")


class TokenizerTest(unittest.TestCase):
    def test_roundtrip_and_specials(self):
        tok = BPETokenizer.train(TEXT, 300)
        self.assertGreater(len(tok.merges), 0)
        sample = "hello <|user|>the lazy 🙂 dog<|end|> snake_case\n\n  x"
        ids = tok.encode(sample)
        self.assertIn(tok.token_id("<|user|>"), ids)
        self.assertEqual(tok.decode(ids), sample)
        self.assertLess(len(tok.encode("the quick brown fox")), len("the quick brown fox"))

    def test_save_load(self):
        tok = BPETokenizer.train(TEXT, 280)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "tok.json")
            tok.save(path)
            tok2 = BPETokenizer.load(path)
        self.assertEqual(tok.encode(TEXT), tok2.encode(TEXT))


class ModelTest(unittest.TestCase):
    def setUp(self):
        ag.seed(0)
        self.tok = BPETokenizer.train(TEXT, 300)
        self.cfg = GPTConfig(self.tok.vocab_size, block_size=32, n_layer=2, n_head=2, n_embd=32, dropout=0.0)

    def test_overfits_small_sequence(self):
        model = GPT(self.cfg)
        ids = np.array(self.tok.encode(TEXT))[:33]
        x, y = ids[None, :-1], ids[None, 1:]
        opt = AdamW(model.parameters(), lr=1e-2, weight_decay=0.0)
        first = None
        for _ in range(60):
            _, loss = model(x, y)
            first = first or loss.item()
            model.zero_grad()
            loss.backward()
            clip_grad_norm(model.parameters(), 1.0)
            opt.step()
        self.assertLess(loss.item(), first * 0.2)

    def test_full_model_gradcheck(self):
        model = GPT(GPTConfig(20, block_size=6, n_layer=1, n_head=2, n_embd=8, dropout=0.0))
        for p in model.parameters():  # float64 for an accurate numeric check
            p.data = p.data.astype(np.float64)
        rng = np.random.default_rng(3)
        x = rng.integers(0, 20, (2, 6))
        y = rng.integers(0, 20, (2, 6))
        _, loss = model(x, y)
        loss.backward()
        p = model.blocks[0].attn.c_attn.weight
        for idx in [(0, 0), (3, 5), (7, 20)]:
            old = p.data[idx]
            p.data[idx] = old + 1e-6
            hi = model(x, y)[1].item()
            p.data[idx] = old - 1e-6
            lo = model(x, y)[1].item()
            p.data[idx] = old
            self.assertAlmostEqual(p.grad[idx], (hi - lo) / 2e-6, places=5)

    def test_checkpoint_roundtrip_and_generate(self):
        model = GPT(self.cfg)
        with tempfile.TemporaryDirectory() as d:
            checkpoint.save(d, model, self.tok, {"step": 1})
            model2, tok2, meta = checkpoint.load(d)
        x = np.array([self.tok.encode("the quick brown")])
        np.testing.assert_allclose(model(x)[0].data, model2(x)[0].data, rtol=1e-6)
        self.assertEqual(meta["step"], 1)
        p = SamplingParams(max_tokens=10, seed=1)
        self.assertEqual(generate(model, self.tok, "the", p), generate(model2, tok2, "the", p))
        out = generate(model, self.tok, "", SamplingParams(max_tokens=5, temperature=0))
        self.assertIsInstance(out, str)


class ServerTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ag.seed(0)
        tok = BPETokenizer.train(TEXT, 300)
        model = GPT(GPTConfig(tok.vocab_size, block_size=32, n_layer=1, n_head=2, n_embd=16, dropout=0.0))
        model.eval()
        cls.server = make_server(Engine(model, tok, "test-model"), "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.server.server_address[1]}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def post(self, path, body):
        req = urllib.request.Request(self.base + path, json.dumps(body).encode(),
                                     {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read().decode()

    def test_models(self):
        with urllib.request.urlopen(self.base + "/v1/models", timeout=10) as r:
            self.assertEqual(json.load(r)["data"][0]["id"], "test-model")

    def test_chat_completion(self):
        res = json.loads(self.post("/v1/chat/completions",
                                   {"messages": [{"role": "user", "content": "hi"}], "max_tokens": 8}))
        self.assertEqual(res["object"], "chat.completion")
        self.assertIn("content", res["choices"][0]["message"])
        self.assertLessEqual(res["usage"]["completion_tokens"], 8)

    def test_streaming(self):
        raw = self.post("/v1/chat/completions", {"messages": [{"role": "user", "content": "hi"}],
                                                 "max_tokens": 6, "stream": True})
        events = [l[6:] for l in raw.splitlines() if l.startswith("data: ")]
        self.assertEqual(events[-1], "[DONE]")
        self.assertIsNotNone(json.loads(events[-2])["choices"][0]["finish_reason"])

    def test_completion(self):
        res = json.loads(self.post("/v1/completions", {"prompt": "the", "max_tokens": 5}))
        self.assertEqual(res["object"], "text_completion")


class ChatFormatTest(unittest.TestCase):
    def test_render(self):
        s = chat.render([{"role": "user", "content": "hi"}], add_generation_prompt=True)
        self.assertEqual(s, "<|user|>hi<|end|><|assistant|>")
        self.assertTrue(chat.render_document({"text": "x"}).endswith(EOT))


if __name__ == "__main__":
    unittest.main()
