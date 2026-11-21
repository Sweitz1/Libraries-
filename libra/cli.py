"""Command line: ``libra train | generate | chat | serve | info``."""
from __future__ import annotations

import argparse
import sys

from . import chat as chat_fmt
from . import checkpoint
from .generate import SamplingParams, stream_text


def _add_sampling(p: argparse.ArgumentParser) -> None:
    p.add_argument("--max-tokens", type=int, default=200)
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--top-k", type=int, default=0)
    p.add_argument("--top-p", type=float, default=0.95)
    p.add_argument("--repetition-penalty", type=float, default=1.1)
    p.add_argument("--seed", type=int, default=None)


def _sampling(args) -> SamplingParams:
    return SamplingParams(args.max_tokens, args.temperature, args.top_k, args.top_p,
                          args.repetition_penalty, args.seed)


def cmd_train(args) -> None:
    from .train import TrainConfig, train

    cfg = TrainConfig(
        data=args.data, out=args.out, vocab_size=args.vocab, block_size=args.ctx, n_layer=args.layers,
        n_head=args.heads, n_embd=args.embd, dropout=args.dropout, batch_size=args.batch, steps=args.steps,
        lr=args.lr, warmup=args.warmup, eval_every=args.eval_every, log_every=args.log_every,
        seed=args.seed, resume=args.resume, sample_prompt=args.sample_prompt,
    )
    train(cfg)


def cmd_generate(args) -> None:
    model, tok, _ = checkpoint.load(args.model)
    sys.stdout.write(args.prompt)
    for piece in stream_text(model, tok, args.prompt, _sampling(args), stop_tokens=chat_fmt.STOP_TOKENS):
        sys.stdout.write(piece)
        sys.stdout.flush()
    sys.stdout.write("\n")


def cmd_chat(args) -> None:
    model, tok, _ = checkpoint.load(args.model)
    params = _sampling(args)
    history: list[dict] = [{"role": "system", "content": args.system}] if args.system else []
    print("libra chat — type /reset to clear, /exit to quit")
    while True:
        try:
            line = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not line:
            continue
        if line in ("/exit", "/quit"):
            return
        if line == "/reset":
            history = history[:1] if args.system else []
            print("(history cleared)")
            continue
        history.append({"role": "user", "content": line})
        prompt = chat_fmt.render(history, add_generation_prompt=True)
        sys.stdout.write("libra> ")
        parts = []
        for piece in stream_text(model, tok, prompt, params, stop_tokens=chat_fmt.STOP_TOKENS):
            parts.append(piece)
            sys.stdout.write(piece)
            sys.stdout.flush()
        sys.stdout.write("\n")
        history.append({"role": "assistant", "content": "".join(parts).strip()})


def cmd_serve(args) -> None:
    from .server import Engine, serve

    model, tok, _ = checkpoint.load(args.model)
    serve(Engine(model, tok, name=args.name), args.host, args.port)


def cmd_info(args) -> None:
    model, tok, meta = checkpoint.load(args.model)
    print(f"parameters: {model.num_parameters():,}")
    print(f"vocab:      {tok.vocab_size}")
    print(f"config:     {model.config.to_dict()}")
    print(f"training:   {meta}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="libra", description="A language model built from scratch. No API keys.")
    sub = parser.add_subparsers(dest="command", required=True)

    t = sub.add_parser("train", help="train a tokenizer and model on your text files")
    t.add_argument("data", nargs="+", help=".txt/.md/.jsonl files or folders")
    t.add_argument("--out", default="checkpoints/model")
    t.add_argument("--vocab", type=int, default=1024, help="tokenizer vocabulary size")
    t.add_argument("--ctx", type=int, default=128, help="context length in tokens")
    t.add_argument("--layers", type=int, default=4)
    t.add_argument("--heads", type=int, default=4)
    t.add_argument("--embd", type=int, default=128)
    t.add_argument("--dropout", type=float, default=0.1)
    t.add_argument("--batch", type=int, default=16)
    t.add_argument("--steps", type=int, default=2000)
    t.add_argument("--lr", type=float, default=3e-3)
    t.add_argument("--warmup", type=int, default=100)
    t.add_argument("--eval-every", type=int, default=200)
    t.add_argument("--log-every", type=int, default=20)
    t.add_argument("--seed", type=int, default=1337)
    t.add_argument("--resume", action="store_true", help="continue from the checkpoint in --out")
    t.add_argument("--sample-prompt", default="", help="text used for the sample printed at each eval")
    t.set_defaults(func=cmd_train)

    g = sub.add_parser("generate", help="continue a prompt")
    g.add_argument("--model", default="checkpoints/model")
    g.add_argument("--prompt", default="")
    _add_sampling(g)
    g.set_defaults(func=cmd_generate)

    c = sub.add_parser("chat", help="interactive chat in the terminal")
    c.add_argument("--model", default="checkpoints/model")
    c.add_argument("--system", default="")
    _add_sampling(c)
    c.set_defaults(func=cmd_chat)

    s = sub.add_parser("serve", help="OpenAI-compatible API server (no key needed)")
    s.add_argument("--model", default="checkpoints/model")
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow phones on your Wi-Fi")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--name", default="libra")
    s.set_defaults(func=cmd_serve)

    i = sub.add_parser("info", help="show a checkpoint's size and settings")
    i.add_argument("--model", default="checkpoints/model")
    i.set_defaults(func=cmd_info)
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
