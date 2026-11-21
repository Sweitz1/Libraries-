"""Repeatable benchmarks, so changes are measured instead of guessed.

    libra eval plan                       # tool choice: does the planner pick the right skills?
    libra eval model checkpoints/web      # the model: held-out loss and behaviour
    libra eval model checkpoints/web --text heldout.txt --suite my-suite.jsonl
    libra eval history                    # every result so far, with changes since the previous one

Lower next-token loss doesn't by itself mean better answers, so the model is measured in
separate ways:

* **Held-out loss / perplexity** on text the model didn't train on (``--text``).
* **Multiple choice by likelihood**: for each item, which of the given continuations the model
  finds most probable (mean log-probability per token, so length doesn't decide). This works
  even for small models, and it measures grammar, common phrases and simple facts.
* **Generation checks** (optional, in your own suite): greedy output must contain one of the
  expected strings, as a test of instruction following.

Suites are JSON Lines. Model items look like ``{"prompt": "The cats", "choices": [" are", " is"],
"answer": 0, "tag": "agreement"}`` or ``{"prompt": "Q: … A:", "expect": ["Paris"]}``. Planner items
look like ``{"goal": "…", "skills": ["scrape", "summarize"]}``, or ``{"goal": "…", "refuse": true}``
for a goal that should not be planned. Built-in suites are in ``libra/evals/``.

Results are appended to ``~/.libra/eval/history.jsonl`` with the checkpoint version, so you can
compare runs and see a regression before it reaches users. ``libra auto`` can require that the
behaviour score doesn't drop before it keeps a newly trained model (``gate.eval_suite``).
"""
from __future__ import annotations

import datetime as _dt
import json
import math
import os

import numpy as np

from . import home

SUITES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evals")
DEFAULT_PLAN_SUITE = os.path.join(SUITES, "planner.jsonl")
DEFAULT_MODEL_SUITE = os.path.join(SUITES, "english_basics.jsonl")


def load_suite(path: str) -> list[dict]:
    items = []
    with open(path, encoding="utf-8") as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("//"):
                continue
            try:
                items.append(json.loads(line))
            except ValueError as exc:
                raise ValueError(f"{path}:{n}: not valid JSON ({exc})") from None
    return items


# ------------------------------------------------------------------- planner
def plan_suite(registry, path: str = DEFAULT_PLAN_SUITE) -> dict:
    """Score the planner's skill choices against a labelled suite."""
    from . import agent

    items = load_suite(path)
    right, steps_right, steps_total, failures = 0, 0, 0, []
    for item in items:
        p = agent.plan(item["goal"], registry, use_procedures=False)  # learned shortcuts would hide planner changes
        got = [s.skill for s in p.steps] if p.ok else None
        if item.get("refuse"):
            ok = got is None
        else:
            want = item["skills"]
            ok = got == want
            steps_total += len(want)
            steps_right += sum(a == b for a, b in zip(got or [], want))
        right += ok
        if not ok:
            failures.append({"goal": item["goal"], "expected": "refuse" if item.get("refuse") else item["skills"],
                             "got": got if got is not None else "refused: " + "; ".join(p.problems)[:200]})
    return {"kind": "plan", "suite": os.path.basename(path), "items": len(items),
            "accuracy": round(right / max(1, len(items)), 4),
            "step_accuracy": round(steps_right / max(1, steps_total), 4), "failures": failures}


# --------------------------------------------------------------------- model
def _logprobs(model, ids: list[int]) -> np.ndarray:
    """log P(ids[i] | ids[:i]) for i >= 1 (the window is the last block_size tokens)."""
    from . import backend as B
    from .autograd import no_grad

    ids = ids[-(model.config.block_size + 1):]
    with no_grad():
        logits, _ = model(np.array([ids[:-1]], dtype=np.int64))
    z = B.to_numpy(logits.data)[0].astype(np.float64)
    z -= z.max(axis=-1, keepdims=True)
    logp = z - np.log(np.exp(z).sum(axis=-1, keepdims=True))
    return logp[np.arange(len(ids) - 1), ids[1:]]


def option_score(model, tokenizer, prompt: str, option: str) -> float:
    """Mean log-probability per token of ``option`` following ``prompt``."""
    from .tokenizer import EOT

    p_ids = tokenizer.encode(EOT + prompt)
    full = tokenizer.encode(EOT + prompt + option)
    k = 0
    while k < min(len(p_ids), len(full)) and p_ids[k] == full[k]:
        k += 1
    k = min(k, len(full) - 1)  # BPE can merge across the boundary; score from where they differ
    n = len(full) - k
    lp = _logprobs(model, full)
    return float(lp[-n:].mean())


def model_suite(model, tokenizer, path: str = DEFAULT_MODEL_SUITE) -> dict:
    from .generate import SamplingParams, generate

    items = load_suite(path)
    choice_total = choice_right = gen_total = gen_right = 0
    by_tag: dict[str, list[int]] = {}
    failures = []
    for item in items:
        if "choices" in item:
            scores = [option_score(model, tokenizer, item["prompt"], c) for c in item["choices"]]
            ok = int(np.argmax(scores)) == int(item.get("answer", 0))
            choice_total += 1
            choice_right += ok
            tag = by_tag.setdefault(item.get("tag", "other"), [0, 0])
            tag[0] += ok
            tag[1] += 1
            if not ok:
                failures.append({"prompt": item["prompt"], "expected": item["choices"][int(item.get("answer", 0))],
                                 "chose": item["choices"][int(np.argmax(scores))]})
        elif "expect" in item:
            out = generate(model, tokenizer, item["prompt"],
                           SamplingParams(max_tokens=int(item.get("max_tokens", 40)), temperature=0, seed=0))
            ok = any(e.lower() in out.lower() for e in item["expect"])
            gen_total += 1
            gen_right += ok
            if not ok:
                failures.append({"prompt": item["prompt"], "expected": item["expect"], "got": out[:120]})
    return {"choice_accuracy": round(choice_right / choice_total, 4) if choice_total else None,
            "choices": choice_total, "chance": _chance(items),
            "generation_pass": round(gen_right / gen_total, 4) if gen_total else None, "generations": gen_total,
            "by_tag": {k: round(v[0] / v[1], 4) for k, v in sorted(by_tag.items())}, "failures": failures[:50]}


def _chance(items) -> float | None:
    odds = [1 / len(i["choices"]) for i in items if "choices" in i]
    return round(sum(odds) / len(odds), 4) if odds else None


def text_loss(model, tokenizer, text: str, max_windows: int = 64) -> dict:
    """Mean next-token loss and perplexity over fixed windows of ``text``."""
    from .autograd import no_grad

    ids = np.array(tokenizer.encode(text), dtype=np.int64)
    block = min(model.config.block_size, len(ids) - 1)
    if block < 2:
        raise ValueError("the text is too short to score")
    starts = np.unique(np.linspace(0, len(ids) - block - 1, num=max_windows).astype(int))
    total = 0.0
    with no_grad():
        for chunk in np.array_split(starts, max(1, len(starts) // 8)):
            x = np.stack([ids[s: s + block] for s in chunk])
            y = np.stack([ids[s + 1: s + block + 1] for s in chunk])
            total += model(x, y)[1].item() * len(chunk)
    loss = total / len(starts)
    return {"loss": round(loss, 4), "perplexity": round(math.exp(min(loss, 50)), 2), "tokens": int(len(ids))}


def evaluate_model(model_dir: str, text_path: str | None = None, suite: str | None = DEFAULT_MODEL_SUITE) -> dict:
    from . import checkpoint

    model, tok, meta = checkpoint.load(model_dir)
    out = {"kind": "model", "model": os.path.abspath(model_dir), "version": checkpoint.version(model_dir),
           "step": meta.get("step"), "parameters": int(model.num_parameters())}
    if text_path:
        with open(text_path, encoding="utf-8") as f:
            out["heldout"] = dict(text_loss(model, tok, f.read()), file=os.path.basename(text_path))
    if suite:
        out["suite"] = os.path.basename(suite)
        out.update(model_suite(model, tok, suite))
    return out


# ------------------------------------------------------------------- history
def _history_path() -> str:
    d = home.path("eval")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "history.jsonl")


def record(result: dict) -> dict:
    """Append a result (without its failure list) and return the previous comparable one, if any."""
    rec = {k: v for k, v in result.items() if k != "failures"}
    rec["at"] = _dt.datetime.now().astimezone().isoformat(timespec="seconds")
    prev = None
    for old in history():
        if old.get("kind") == rec["kind"] and old.get("suite") == rec.get("suite") and \
                (rec["kind"] == "plan" or old.get("model") == rec.get("model")):
            prev = old
    with open(_history_path(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return prev


def history() -> list[dict]:
    try:
        with open(_history_path(), encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]
    except FileNotFoundError:
        return []


def headline(rec: dict) -> dict:
    """The numbers worth comparing between runs."""
    if rec.get("kind") == "plan":
        return {"accuracy": rec.get("accuracy"), "step_accuracy": rec.get("step_accuracy")}
    out = {"choice_accuracy": rec.get("choice_accuracy"), "generation_pass": rec.get("generation_pass")}
    if rec.get("heldout"):
        out["heldout_loss"] = rec["heldout"]["loss"]
    return out


def describe(result: dict, prev: dict | None = None) -> str:
    lines = []
    now, before = headline(result), headline(prev) if prev else {}
    for key, value in now.items():
        if value is None:
            continue
        change = ""
        if before.get(key) is not None:
            delta = value - before[key]
            better = delta < 0 if key == "heldout_loss" else delta > 0
            change = "  (unchanged)" if abs(delta) < 1e-9 else f"  ({delta:+.4f}, {'better' if better else 'worse'})"
        lines.append(f"  {key:<16} {value}{change}")
    if result.get("kind") == "plan":
        lines.insert(0, f"planner on {result['suite']}: {result['items']} goals")
    else:
        lines.insert(0, f"model {result['model']} (version {result['version']}, {result['parameters']:,} parameters)")
        if result.get("heldout"):
            lines.append(f"  perplexity       {result['heldout']['perplexity']} on {result['heldout']['file']}")
        if result.get("chance") is not None:
            lines.append(f"  chance level     {result['chance']} (what random guessing scores)")
        for tag, acc in (result.get("by_tag") or {}).items():
            lines.append(f"    {tag:<14} {acc}")
    if result.get("failures"):
        lines.append(f"  {len(result['failures'])} miss(es), first few:")
        for f in result["failures"][:8]:
            lines.append("    - " + json.dumps(f, ensure_ascii=False)[:200])
    return "\n".join(lines)
