"""Benchmarks: planner tool choice, likelihood scoring, history with deltas, and the behaviour gate
that stops unattended training from keeping a model whose answers got worse."""
import json
import os
import tempfile
import time
import unittest
from unittest import mock


class EvalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = os.path.join(self.tmp.name, "home")

    def tearDown(self):
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def write(self, name, rows):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write("// a comment line\n" + "\n".join(json.dumps(r) for r in rows) + "\n")
        return path

    def test_planner_suite_scores_choices_and_refusals(self):
        from libra import evaluate
        from libra.skills import default_registry

        reg = default_registry(log=None, load_default_dir=False, catalog=None)
        suite = self.write("plan.jsonl", [
            {"goal": "what is 2+2", "skills": ["calc"]},
            {"goal": "scrape https://example.com then summarize it", "skills": ["scrape", "summarize"]},
            {"goal": "scrape https://example.com then summarize it", "skills": ["scrape", "translate"]},  # wrong label
            {"goal": "frobnicate the flux", "refuse": True},
        ])
        r = evaluate.plan_suite(reg, suite)
        self.assertEqual((r["items"], r["accuracy"], r["step_accuracy"]), (4, 0.75, 0.8))
        self.assertEqual(r["failures"][0]["got"], ["scrape", "summarize"])
        builtin = evaluate.plan_suite(reg)  # the shipped suite runs, and the planner does well on it
        self.assertGreaterEqual(builtin["accuracy"], 0.9)

    def test_likelihood_scoring_and_history(self):
        from libra import autograd as ag
        from libra import checkpoint, evaluate
        from libra.nn import GPT, GPTConfig
        from libra.optim import AdamW
        from libra.tokenizer import BPETokenizer

        import numpy as np

        text = "the cat is on the mat. " * 40
        tok = BPETokenizer.train(text, 300)
        ag.seed(0)
        model = GPT(GPTConfig(tok.vocab_size, 32, 1, 2, 32, 0.0))
        ids = np.array(tok.encode(text))
        opt = AdamW(model.parameters(), lr=0.01)
        for _ in range(60):  # learn the one sentence
            s = np.random.randint(0, len(ids) - 33, 8)
            _, loss = model(np.stack([ids[i:i + 32] for i in s]), np.stack([ids[i + 1:i + 33] for i in s]))
            model.zero_grad()
            loss.backward()
            opt.step()
        model.eval()
        self.assertGreater(evaluate.option_score(model, tok, "the cat is on the", " mat."),
                           evaluate.option_score(model, tok, "the cat is on the", " zebra."))
        out = os.path.join(self.tmp.name, "m")
        checkpoint.save(out, model, tok, {"step": 60})
        suite = self.write("s.jsonl", [{"prompt": "the cat is on the", "choices": [" mat.", " zebra."], "answer": 0,
                                        "tag": "seen"},
                                       {"prompt": "the cat is", "expect": ["on"]}])
        held = os.path.join(self.tmp.name, "held.txt")
        with open(held, "w") as f:
            f.write(text)
        r = evaluate.evaluate_model(out, held, suite)
        self.assertEqual((r["choice_accuracy"], r["by_tag"], r["chance"]), (1.0, {"seen": 1.0}, 0.5))
        self.assertEqual(r["generation_pass"], 1.0)
        self.assertLess(r["heldout"]["loss"], 1.5)
        self.assertIsNone(evaluate.record(r))  # nothing to compare with yet
        prev = evaluate.record(dict(r, choice_accuracy=0.5))
        self.assertEqual(prev["choice_accuracy"], 1.0)
        self.assertIn("worse", evaluate.describe(dict(r, choice_accuracy=0.5), prev))
        self.assertEqual(len(evaluate.history()), 2)
        builtin = evaluate.evaluate_model(out)  # the shipped behaviour suite runs on any checkpoint
        self.assertEqual(builtin["choices"], 40)

    def test_training_gate_rejects_a_model_whose_behaviour_dropped(self):
        from libra import auto, checkpoint
        from libra.nn import GPT, GPTConfig
        from libra.tokenizer import BPETokenizer

        tok = BPETokenizer.train("hello world " * 30, 280)
        model_dir = os.path.join(self.tmp.name, "model")
        checkpoint.save(model_dir, GPT(GPTConfig(tok.vocab_size, 16, 1, 2, 16, 0.0)), tok, {"step": 5})
        before = checkpoint.version(model_dir)
        cfg = json.loads(json.dumps(auto.TEMPLATE))
        cfg["gate"]["eval_suite"] = "builtin"
        scores = {"candidate": 0.40, "model": 0.55}

        def fake_eval(path, text=None, suite=None):
            return {"choice_accuracy": scores["candidate" if path.endswith(".candidate") else "model"]}

        with mock.patch("libra.train.train"), mock.patch.object(auto, "holdout_loss", return_value=3.0), \
                mock.patch("libra.evaluate.evaluate_model", side_effect=fake_eval):
            report = auto.Report(trained=True)
            auto._train_and_gate(cfg, self.tmp.name, ["text"], model_dir, report, time.time(), None)
            self.assertIn("behaviour score dropped", report.decision)
            self.assertEqual(checkpoint.version(model_dir), before)  # the current model stays
            self.assertTrue(os.path.exists(model_dir + ".rejected"))
            self.assertIn("behaviour (eval suite accuracy): old 0.550 → new 0.400", report.markdown(cfg))

            cfg["gate"]["max_eval_drop"] = 0.2  # within the allowed drop: kept
            report = auto.Report()
            auto._train_and_gate(cfg, self.tmp.name, ["text"], model_dir, report, time.time(), None)
            self.assertTrue(report.decision.startswith("kept the new model"), report.decision)

        cfg["gate"]["eval_suite"] = "/no/such/suite.jsonl"
        with self.assertRaises(ValueError):
            auto._eval_suite(cfg)


if __name__ == "__main__":
    unittest.main()
