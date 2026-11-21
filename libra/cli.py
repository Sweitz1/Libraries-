"""Command line: ``libra train | generate | chat | serve | info``."""
from __future__ import annotations

import argparse
import sys

from . import chat as chat_fmt
from . import backend as B
from . import checkpoint
from .generate import SamplingParams, stream_text


def _add_sampling(p: argparse.ArgumentParser) -> None:
    p.add_argument("--max-tokens", type=int, default=200)
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--top-k", type=int, default=0)
    p.add_argument("--top-p", type=float, default=0.95)
    p.add_argument("--repetition-penalty", type=float, default=1.1)
    p.add_argument("--seed", type=int, default=None)


def _add_device(p: argparse.ArgumentParser) -> None:
    p.add_argument("--device", default="auto", choices=["auto", "cpu", "gpu"],
                   help="gpu needs an NVIDIA GPU and CuPy (pip install cupy-cuda12x)")


def _sampling(args) -> SamplingParams:
    return SamplingParams(args.max_tokens, args.temperature, args.top_k, args.top_p,
                          args.repetition_penalty, args.seed)


def cmd_train(args) -> None:
    from .train import TrainConfig, train

    cfg = TrainConfig(
        data=args.data, out=args.out, vocab_size=args.vocab, block_size=args.ctx, n_layer=args.layers,
        n_head=args.heads, n_embd=args.embd, dropout=args.dropout, batch_size=args.batch, steps=args.steps,
        lr=args.lr, warmup=args.warmup, eval_every=args.eval_every, log_every=args.log_every,
        seed=args.seed, resume=args.resume, sample_prompt=args.sample_prompt, device=args.device,
    )
    train(cfg)


def cmd_generate(args) -> None:
    B.use(args.device)
    model, tok, _ = checkpoint.load(args.model)
    sys.stdout.write(args.prompt)
    for piece in stream_text(model, tok, args.prompt, _sampling(args), stop_tokens=chat_fmt.STOP_TOKENS):
        sys.stdout.write(piece)
        sys.stdout.flush()
    sys.stdout.write("\n")


def cmd_chat(args) -> None:
    B.use(args.device)
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

    print(f"device: {B.use(args.device)}")
    model, tok, _ = checkpoint.load(args.model)
    serve(Engine(model, tok, name=args.name), args.host, args.port)


def cmd_data(args) -> None:
    from .dataset import Dataset

    ds = Dataset(min_chars=args.min_chars)
    print("reading sources:")
    for path in args.inputs:
        ds.add_path(path)
    for url in args.url:
        try:
            ds.add_url(url)
        except Exception as exc:  # keep going with the other sources
            print(f"  {url}: download failed ({exc})")
    for book in args.gutenberg:
        try:
            ds.add_gutenberg(book)
        except Exception as exc:
            print(f"  gutenberg #{book}: download failed ({exc})")
    if not ds.texts and not ds.chats:
        raise SystemExit("no usable text found; check the paths or URLs")
    stats = ds.save(args.out)
    print(f"\nwrote {args.out}: {stats['documents']} documents, {stats['conversations']} conversations, "
          f"{stats['characters']:,} characters (~{stats['estimated_tokens']:,} tokens), "
          f"{stats['duplicates_removed']} duplicates removed, {stats['skipped']} skipped")
    print(f"suggested ({stats['suggestion']['preset']}):\n  {stats['suggestion']['command']}")


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
    _add_device(t)
    t.set_defaults(func=cmd_train)

    g = sub.add_parser("generate", help="continue a prompt")
    g.add_argument("--model", default="checkpoints/model")
    g.add_argument("--prompt", default="")
    _add_sampling(g)
    _add_device(g)
    g.set_defaults(func=cmd_generate)

    c = sub.add_parser("chat", help="interactive chat in the terminal")
    c.add_argument("--model", default="checkpoints/model")
    c.add_argument("--system", default="")
    _add_sampling(c)
    _add_device(c)
    c.set_defaults(func=cmd_chat)

    s = sub.add_parser("serve", help="OpenAI-compatible API server (no key needed)")
    s.add_argument("--model", default="checkpoints/model")
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow phones on your Wi-Fi")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--name", default="libra")
    _add_device(s)
    s.set_defaults(func=cmd_serve)

    d = sub.add_parser("data", help="build a clean training dataset from files, web pages and books")
    d.add_argument("inputs", nargs="*", help="files or folders: .txt .md .html .json .jsonl .csv "
                   "(chat exports, ShareGPT, Alpaca and Q/A tables are detected)")
    d.add_argument("--url", action="append", default=[], help="web page or text file to download (repeatable)")
    d.add_argument("--gutenberg", action="append", type=int, default=[],
                   help="Project Gutenberg book number to download (repeatable), e.g. 1342")
    d.add_argument("--out", default="datasets/mine")
    d.add_argument("--min-chars", type=int, default=40, help="drop documents shorter than this")
    d.set_defaults(func=cmd_data)

    i = sub.add_parser("info", help="show a checkpoint's size and settings")
    i.add_argument("--model", default="checkpoints/model")
    i.set_defaults(func=cmd_info)
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except (RuntimeError, FileNotFoundError, ValueError) as exc:
        raise SystemExit(f"libra: error: {exc}") from None


if __name__ == "__main__":
    main()
