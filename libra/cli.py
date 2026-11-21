"""Command line: ``libra train | generate | chat | serve | info``."""
from __future__ import annotations

import argparse
import json
import os
import shlex
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


def _add_workspace_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--workspace", help="answer only from the files in this workspace (see libra kb)")
    p.add_argument("--grounded", action="store_true", help="same, using the default workspace ~/.libra/workspace")


def _add_skill_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--plugins", action="append", default=[], help="plugin .py file or folder (repeatable)")
    p.add_argument("--allow-private", action="store_true", help="let web skills reach local/LAN addresses")


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


def _workspace(args):
    from .knowledge import DEFAULT_WORKSPACE, Workspace

    path = getattr(args, "workspace", None)
    return Workspace(path or DEFAULT_WORKSPACE) if (path or getattr(args, "grounded", False)) else None


def _workspace_command(ws, line: str, last_question: str | None) -> bool:
    """Handle /add /correct /rule /rules /sources /forget. Returns True if handled."""
    cmd, _, rest = line.partition(" ")
    rest = rest.strip()
    if cmd == "/add":
        if not rest:
            print("usage: /add <file or folder> [...]")
            return True
        try:
            report = ws.add(shlex.split(rest))
        except FileNotFoundError as exc:
            print(f"(not found: {exc})")
            return True
        _print_add_report(report)
    elif cmd == "/correct":
        if not (rest and last_question):
            print("usage: /correct <the right answer>  (corrects the answer to your last question)")
        else:
            ws.correct(last_question, rest)
            print(f"(saved: from now on, \"{last_question}\" is answered with your correction)")
    elif cmd == "/rule":
        if not rest:
            print("usage: /rule <instruction>, e.g. /rule always answer in bullet points")
        else:
            ws.add_rule(rest)
            st = ws.style
            print(f"(rule saved; style is now: {'bullets' if st.bullets else 'paragraph'}, "
                  f"up to {st.max_sentences} sentence(s), clarifying questions {'on' if st.clarify else 'off'})")
    elif cmd == "/rules":
        print("\n".join(f"{i}. {r['text']}  ({r['date'][:10]})" for i, r in enumerate(ws.rules, 1)) or "(no rules)")
    elif cmd == "/sources":
        print(ws.summary())
    elif cmd == "/forget":
        print("(removed)" if rest and ws.remove(rest) else "usage: /forget <file path as shown in /sources>")
    else:
        return False
    return True


def _print_add_report(report) -> None:
    print(f"added {len(report['added'])} file(s), {len(report['unchanged'])} unchanged")
    if report["media_without_text"]:
        print("media I can't read directly (add a .vtt/.srt transcript or .txt description with the same name):")
        for f in report["media_without_text"]:
            print(f"  {f}")
    if report["unsupported"]:
        print("unsupported file types (skipped): " + ", ".join(os.path.basename(f) for f in report["unsupported"][:10])
              + (" …" if len(report["unsupported"]) > 10 else ""))


def cmd_chat(args) -> None:
    ws = _workspace(args)
    model = tok = None
    if ws is None:
        B.use(args.device)
        model, tok, _ = checkpoint.load(args.model)
    params = _sampling(args)
    skills = _registry(args)
    history: list[dict] = [{"role": "system", "content": args.system}] if args.system else []
    last_question = None
    if ws is not None:
        print(f"libra grounded chat — answers come only from your files ({len(ws.sources)} indexed in {ws.path})")
        print("/add <path>, /correct <answer>, /rule <instruction>, /rules, /sources, /skills, /reset, /exit")
    else:
        print("libra chat — /skills lists tools, /reset clears, /exit quits")
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
        if line in ("/skills", "/help"):
            print(_skills_help(skills))
            continue
        if line.startswith("/find "):
            found = skills.search(line[6:], 10)
            print("\n".join(_skill_line(s) for s in found) or "(no matching skills)")
            continue
        if line.startswith("/info "):
            try:
                print(_skill_info(skills, line[6:].strip()))
            except KeyError as exc:
                print(f"(skill error: {exc})")
            continue
        if ws is not None and line.startswith("/") and _workspace_command(ws, line, last_question):
            continue
        call = None
        if line.startswith("/"):
            try:
                call = skills.parse_command(line)
            except (KeyError, ValueError) as exc:
                print(f"(skill error: {exc})")
                continue
        elif not args.no_auto_skills:
            call = skills.route(line)
        if call:
            # Skills answer directly: a small from-scratch model can't be trusted to restate facts.
            result = skills.run(*call)
            print(f"[{call[0]}{'' if result.ok else ' failed'}]\n{result.text}")
            history.append({"role": "user", "content": line})
            history.append({"role": "assistant", "content": result.text[:500]})
            continue
        history.append({"role": "user", "content": line})
        if ws is not None:
            from .knowledge import CLARIFY_MARKER

            follows_up = len(history) >= 3 and history[-2]["content"].startswith(CLARIFY_MARKER)
            answer = ws.ask_chat(history)
            if answer.status == "answered":  # what /correct will correct
                last_question = history[-3]["content"] if follows_up and answer.text else line
            print(f"libra> {answer.text}")
            history.append({"role": "assistant", "content": answer.text})
            continue
        prompt = chat_fmt.render(history, add_generation_prompt=True)
        sys.stdout.write("libra> ")
        parts = []
        for piece in stream_text(model, tok, prompt, params, stop_tokens=chat_fmt.STOP_TOKENS):
            parts.append(piece)
            sys.stdout.write(piece)
            sys.stdout.flush()
        sys.stdout.write("\n")
        history.append({"role": "assistant", "content": "".join(parts).strip()})


def cmd_kb(args) -> None:
    from .knowledge import DEFAULT_WORKSPACE, Workspace

    ws = Workspace(args.workspace or DEFAULT_WORKSPACE)
    a, rest = args.action, args.items
    if a == "add":
        if not rest:
            raise SystemExit("usage: libra kb add <file or folder> [...]")
        _print_add_report(ws.add(rest))
    elif a == "ask":
        answer = ws.ask(" ".join(rest), clarify=not args.no_clarify)
        print(json.dumps(answer.to_dict(), indent=2, ensure_ascii=False) if args.json else answer.text)
    elif a == "correct":
        if len(rest) != 2:
            raise SystemExit('usage: libra kb correct "question" "right answer"')
        ws.correct(*rest)
        print("(correction saved)")
    elif a == "rule":
        ws.add_rule(" ".join(rest))
        print("(rule saved)")
    elif a == "rules":
        print("\n".join(f"{i}. {r['text']}" for i, r in enumerate(ws.rules, 1)) or "(no rules)")
    elif a == "list":
        print(ws.summary())
    elif a == "remove":
        for path in rest:
            print(("removed " if ws.remove(path) else "not indexed: ") + path)
    elif a == "export":
        out = rest[0] if rest else "datasets/workspace"
        stats = ws.export_training(out)
        print(f"wrote {stats['documents']} document(s) and {stats['conversations']} correction(s) to {out}\n"
              f"train on them with: libra train {out} --out checkpoints/mine")


def cmd_serve(args) -> None:
    from .server import Engine, serve

    print(f"device: {B.use(args.device)}")
    ws = _workspace(args)
    model = tok = None
    if ws is None or os.path.exists(os.path.join(args.model, "model.npz")):
        model, tok, _ = checkpoint.load(args.model)
    skills = _registry(args, interactive=False)  # "ask" skills are refused over HTTP; use libra trust allow
    if ws is not None:
        print(f"grounded: chat answers come from {len(ws.sources)} file(s) in {ws.path}")
    serve(Engine(model, tok, name=args.name, skills=skills, auto_skills=args.auto_skills, workspace=ws),
          args.host, args.port)


def _registry(args, interactive: bool = True):
    """Skills with the trust policy applied. ``interactive``: ask on the terminal for "ask" decisions."""
    from .skills import default_registry
    from .trust import Policy, console_confirm

    if getattr(args, "allow_private", False):
        os.environ["LIBRA_ALLOW_PRIVATE"] = "1"
    confirm = None
    if getattr(args, "yes", False):
        confirm = lambda skill, a: True  # noqa: E731  (--yes approves "ask"; "deny" rules still apply)
    elif interactive and sys.stdin.isatty():
        confirm = console_confirm
    return default_registry(getattr(args, "plugins", None) or [], log=print, policy=Policy(), confirm=confirm)


def _skill_line(s) -> str:
    desc = s.description if len(s.description) <= 90 else s.description[:89] + "…"
    return f"  /{s.name} {s.usage()}".rstrip() + f"  [{s.risk}] — {desc}"


def _skill_info(skills, name: str) -> str:
    s = skills.get(name)
    decision, reason = skills.policy.decide(s.name, s.risk) if skills.policy else ("allow", "no policy")
    return "\n".join([f"{s.name}  ({s.source})", f"  {s.description}", f"  usage: /{s.name} {s.usage()}",
                      f"  risk: {s.risk}; trust: {decision} ({reason})",
                      "  parameters: " + json.dumps(s.schema()["function"]["parameters"], indent=2).replace(
                          "\n", "\n  ")])


def _skills_help(skills) -> str:
    lines = ["skills (use /name args, or just ask, e.g. \"scrape https://example.com\"):"]
    for s in skills.list():
        lines.append(_skill_line(s))
    lines.append("  /find <words>  — search the skill catalog (MCP tools, API operations); /info <name> for details")
    return "\n".join(lines)


def cmd_skill(args) -> None:
    skills = _registry(args)
    if args.action == "list":
        print(_skills_help(skills))
        if skills.catalog is not None:
            n = skills.catalog.count()
            print(f"\ncatalog: {n:,} more skill(s) from {len(skills.catalog.sources())} source(s); "
                  "search them with: libra skill find <words>")
    elif args.action == "schema":
        print(json.dumps([s.schema() for s in skills.list()], indent=2))
    elif args.action == "find":
        query = " ".join([args.name or "", *args.args]).strip()
        if not query:
            raise SystemExit("usage: libra skill find <words>")
        found = skills.search(query, args.limit)
        print("\n".join(_skill_line(s) for s in found) or "(no matching skills)")
    elif args.action == "info":
        if not args.name:
            raise SystemExit("usage: libra skill info NAME")
        print(_skill_info(skills, args.name))
    else:
        if not args.name:
            raise SystemExit("usage: libra skill run NAME [ARG ...] [key=value ...]")
        name, kwargs = skills.parse_command(shlex.join([args.name, *args.args]))
        result = skills.run(name, kwargs)
        print(result.text)
        if not result.ok:
            raise SystemExit(1)


def cmd_mcp(args) -> None:
    from . import mcp
    from .catalog import Catalog

    servers = mcp.load_config()
    if args.action == "list":
        if not servers:
            print("no MCP servers; add one with: libra mcp add NAME -- command args   or   --url URL")
        counts = dict(Catalog().sources()) if servers else {}
        for name, cfg in sorted(servers.items()):
            where = cfg.get("url") or " ".join([cfg.get("command", ""), *cfg.get("args", [])])
            n = counts.get(f"mcp.{mcp.safe_name(name)}", 0)
            print(f"  {name}: {where}  ({n} tool(s) in catalog)")
        return
    if not args.name:
        raise SystemExit(f"usage: libra mcp {args.action} NAME")
    if args.action == "add":
        cmd = [c for c in args.command if c != "--"]
        if bool(cmd) == bool(args.url):
            raise SystemExit("give either a command (after --) or --url")
        cfg = {"url": args.url} if args.url else {"command": cmd[0], "args": cmd[1:]}
        if args.header:
            cfg["headers"] = dict(h.split(":", 1) for h in args.header)
            cfg["headers"] = {k.strip(): v.strip() for k, v in cfg["headers"].items()}
        if args.env:
            cfg["env"] = dict(e.split("=", 1) for e in args.env)
        servers[args.name] = cfg
        mcp.save_config(servers)
        print(f"added MCP server {args.name}; connecting to list its tools…")
        mcp.sync(args.name)
        print(f"its tools are new skills named mcp.{mcp.safe_name(args.name)}.<tool> — see: libra skill find <words>")
    elif args.action == "remove":
        servers.pop(args.name, None)
        mcp.save_config(servers)
        print(f"removed {args.name} and {Catalog().remove_source('mcp.' + mcp.safe_name(args.name))} tool(s)")
    else:
        mcp.sync(None if args.name == "all" else args.name)


def cmd_api(args) -> None:
    from . import openapi

    if args.allow_private:
        os.environ["LIBRA_ALLOW_PRIVATE"] = "1"
    if args.action == "list":
        cfg = openapi.load_configs()
        if not cfg:
            print("no APIs; import one with: libra api add NAME spec.yaml")
        for name, c in sorted(cfg.items()):
            auth = c.get("auth", {}).get("type", "none")
            print(f"  {name}: {c.get('title', '')} — {c.get('operations', 0)} operation(s), {c['base_url']}, auth: {auth}")
    elif args.action == "add":
        if not (args.name and args.spec):
            raise SystemExit("usage: libra api add NAME SPEC [--base-url URL] [--bearer-env VAR ...]")
        auth = None
        if args.bearer_env:
            auth = {"type": "bearer", "env": args.bearer_env}
        elif args.header_env:
            auth = {"type": "header", "name": args.header_env[0], "env": args.header_env[1]}
        elif args.query_env:
            auth = {"type": "query", "name": args.query_env[0], "env": args.query_env[1]}
        elif args.basic_env:
            auth = {"type": "basic", "env_user": args.basic_env[0], "env_pass": args.basic_env[1]}
        n = openapi.add_api(args.name, args.spec, args.base_url, auth)
        print(f"imported {n} operation(s) as api.{openapi.safe_name(args.name)}.<operation>")
    elif args.action == "add-dir":
        if not args.name:
            raise SystemExit("usage: libra api add-dir FOLDER")
        out = openapi.add_dir(args.name)
        total = sum(out["imported"].values())
        print(f"imported {len(out['imported'])} API(s) with {total:,} operation(s); {len(out['failed'])} skipped")
    else:
        if not args.name:
            raise SystemExit("usage: libra api remove NAME")
        print(f"removed {openapi.remove_api(args.name)} operation(s)")


def cmd_trust(args) -> None:
    from . import home
    from .trust import Policy

    policy = Policy()
    if args.action == "list":
        print("defaults: " + ", ".join(f"{k} = {v}" for k, v in policy.defaults.items()))
        for target, decision in sorted(policy.rules.items()):
            print(f"  {target}: {decision}")
        if not policy.rules:
            print("  (no rules; add one with: libra trust allow|ask|deny TARGET)")
    elif args.action == "log":
        path = home.path("audit.jsonl")
        if not os.path.exists(path):
            print("(no skill calls logged yet)")
            return
        with open(path, encoding="utf-8") as f:
            lines = f.readlines()[-args.n:]
        for line in lines:
            e = json.loads(line)
            status = {True: "ok", False: "failed", None: "-"}[e.get("ok")]
            print(f"{e['time']}  {e['decision']:<8} {status:<6} {e['skill']}  {json.dumps(e.get('args', {}))[:120]}")
    else:
        if not args.target:
            raise SystemExit(f"usage: libra trust {args.action} TARGET")
        if args.action == "reset":
            print("reset" if policy.reset(args.target) else f"no rule for {args.target}")
        else:
            policy.set(args.target, args.action)
            print(f"{args.target}: {args.action}")


def cmd_translate(args) -> None:
    from .lang import code_for, name, romanize
    from .translate import Translator, read_pairs

    t = Translator()
    a, items = args.action, args.items

    def lang(value, what):
        code = code_for(value or "")
        if not code:
            raise SystemExit(f"give --{what} as a language name or code (e.g. en, Spanish)")
        return code

    if a == "text":
        text = " ".join(items) if items else sys.stdin.read()
        if not args.to:
            raise SystemExit("usage: libra translate text \"…\" --to LANGUAGE [--from LANGUAGE]")
        result = t.translate(text, args.to, getattr(args, "from_") or "auto")
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) if args.json else result.describe())
    elif a == "add":
        if not items:
            raise SystemExit("usage: libra translate add FILE [SECOND_FILE] --src en --tgt es")
        src, tgt = lang(args.src, "src"), lang(args.tgt, "tgt")
        second = items[1] if len(items) > 1 else None
        n = t.add_pairs(read_pairs(items[0], src, tgt, second), src, tgt, os.path.basename(items[0]), log=print)
        print(f"added {n:,} new {name(src)}–{name(tgt)} sentence pair(s); {t.pairs_for(src, tgt):,} in total")
        print(f"next: libra translate align --src {src} --tgt {tgt}   (learn a word dictionary from them)")
    elif a == "dict":
        if not items:
            raise SystemExit("usage: libra translate dict WORDLIST.tsv --src en --tgt es")
        print(f"added {t.add_dictionary(items[0], lang(args.src, 'src'), lang(args.tgt, 'tgt')):,} dictionary entries")
    elif a == "align":
        src, tgt = lang(args.src, "src"), lang(args.tgt, "tgt")
        n = t.align(src, tgt, args.max_pairs, args.iterations, log=print)
        print(f"learned {n:,} {name(src)}→{name(tgt)} word translations (IBM Model 1, both directions agreeing)")
    elif a == "prepare":
        out = items[0] if items else "datasets/translate"
        src, tgt = lang(args.src, "src"), lang(args.tgt, "tgt")
        n = t.export_training(out, src, tgt, args.both, args.max_pairs)
        print(f"wrote {n:,} training example(s) to {out}\n"
              f"train:  libra train {out} --out checkpoints/translate_{src}_{tgt} --ctx 128\n"
              f"use it: libra translate model checkpoints/translate_{src}_{tgt} --src {src} --tgt {tgt}")
    elif a == "model":
        src, tgt = lang(args.src, "src"), lang(args.tgt, "tgt")
        path = items[0] if items else None
        if path and not os.path.exists(os.path.join(path, "model.npz")):
            raise SystemExit(f"no trained model in {path}")
        t.set_model(src, tgt, path)
        print(f"{name(src)}→{name(tgt)} model: {path or '(removed)'}")
    elif a == "detect":
        text = " ".join(items)
        for code, conf in t.detect(text)[:5]:
            print(f"{name(code)} ({code}): {round(conf * 100)}%")
    elif a == "romanize":
        print(romanize(" ".join(items)))
    else:
        st = t.stats()
        print("sentence pairs:" if st["pairs"] else "no sentence pairs yet (libra translate add FILE --src en --tgt es)")
        for la, lb, n in st["pairs"]:
            print(f"  {name(la)}–{name(lb)}: {n:,}")
        for src, tgt, origin, n in st["dictionary"]:
            print(f"dictionary {name(src)}→{name(tgt)} ({'your list' if origin == 'user' else 'learned'}): {n:,} entries")
        for src, tgt, path in st["models"]:
            print(f"model {name(src)}→{name(tgt)}: {path}")


def cmd_scrape(args) -> None:
    from .web import Fetcher, scrape, to_markdown

    fetcher = Fetcher(allow_private=args.allow_private, respect_robots=not args.ignore_robots)
    result = scrape(args.url, args.select or None, fetcher)
    if args.format == "json":
        print(json.dumps(result, indent=2, ensure_ascii=False))
    elif args.select:
        for sel, values in result["selected"].items():
            for v in values:
                print(v)
    elif args.format == "text":
        print(result["text"])
    else:
        print(to_markdown(result))


def cmd_crawl(args) -> None:
    from .web import Fetcher, crawl

    fetcher = Fetcher(delay=args.delay, allow_private=args.allow_private, respect_robots=not args.ignore_robots)
    out = open(args.out, "w", encoding="utf-8") if args.out else None
    n = 0
    try:
        for page in crawl(args.url, args.max_pages, args.depth, args.delay, not args.any_site, args.include,
                          args.exclude, fetcher, on_error=lambda u, e: print(f"  skip {u}: {e}", file=sys.stderr)):
            n += 1
            print(f"[{n}] depth {page.depth} | {len(page.text):>7,} chars | {page.url}", file=sys.stderr)
            record = {"url": page.url, "title": page.title, "text": page.text}
            if out:
                out.write(json.dumps(record, ensure_ascii=False) + "\n")
            else:
                print(json.dumps(record, ensure_ascii=False))
    finally:
        if out:
            out.close()
    print(f"crawled {n} page(s)" + (f" -> {args.out} (use it with: libra data {args.out})" if args.out else ""),
          file=sys.stderr)


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
    for url in args.crawl:
        from .web import Fetcher, crawl

        fetcher = Fetcher(delay=args.crawl_delay, allow_private=args.allow_private)
        before = len(ds.texts)
        for page in crawl(url, args.crawl_pages, args.crawl_depth, args.crawl_delay, fetcher=fetcher,
                          on_error=lambda u, e: print(f"  skip {u}: {e}")):
            ds.add_text((page.title + "\n\n" if page.title else "") + page.text)
        print(f"  crawl {url}: +{len(ds.texts) - before} docs")
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
    _add_skill_flags(c)
    c.add_argument("--no-auto-skills", action="store_true", help="only run skills via /commands")
    _add_workspace_flags(c)
    c.set_defaults(func=cmd_chat)

    s = sub.add_parser("serve", help="OpenAI-compatible API server (no key needed)")
    s.add_argument("--model", default="checkpoints/model")
    s.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 to allow phones on your Wi-Fi")
    s.add_argument("--port", type=int, default=8000)
    s.add_argument("--name", default="libra")
    _add_device(s)
    _add_skill_flags(s)
    s.add_argument("--auto-skills", action="store_true",
                   help="answer chat messages like 'scrape https://...' or '2+2' with the matching skill")
    _add_workspace_flags(s)
    s.set_defaults(func=cmd_serve)

    k = sub.add_parser("skill", help="list, describe or run skills (built-in and plugins)")
    k.add_argument("action", choices=["list", "find", "info", "run", "schema"])
    k.add_argument("name", nargs="?")
    k.add_argument("args", nargs="*", help="positional values and/or key=value")
    k.add_argument("--limit", type=int, default=15, help="find: number of results")
    k.add_argument("--yes", action="store_true", help="run: approve a skill that would ask (deny rules still apply)")
    _add_skill_flags(k)
    k.set_defaults(func=cmd_skill)

    m = sub.add_parser("mcp", help="connect MCP servers and use their tools as skills")
    m.add_argument("action", choices=["add", "remove", "list", "sync"])
    m.add_argument("name", nargs="?")
    m.add_argument("command", nargs=argparse.REMAINDER, help="add: the command that starts the server, after --")
    m.add_argument("--url", help="add: Streamable HTTP endpoint instead of a command")
    m.add_argument("--header", action="append", default=[], help='add: "Name: value" (use ${VAR} for secrets)')
    m.add_argument("--env", action="append", default=[], help="add: NAME=value for the server process")
    m.set_defaults(func=cmd_mcp)

    ap = sub.add_parser("api", help="import OpenAPI/Swagger specs: one skill per endpoint")
    ap.add_argument("action", choices=["add", "add-dir", "remove", "list"])
    ap.add_argument("name", nargs="?", help="add: short name; add-dir: folder of specs")
    ap.add_argument("spec", nargs="?", help="add: spec file path or URL (JSON or YAML)")
    ap.add_argument("--base-url", help="server URL if the spec has none")
    ap.add_argument("--bearer-env", help="env var holding a bearer token")
    ap.add_argument("--header-env", nargs=2, metavar=("HEADER", "ENV"), help="send env var ENV in header HEADER")
    ap.add_argument("--query-env", nargs=2, metavar=("PARAM", "ENV"), help="send env var ENV as query param")
    ap.add_argument("--basic-env", nargs=2, metavar=("USER_ENV", "PASS_ENV"), help="basic auth from two env vars")
    ap.add_argument("--allow-private", action="store_true", help="allow local/LAN spec URLs")
    ap.set_defaults(func=cmd_api)

    tr = sub.add_parser("trust", help="allow, ask or deny skills by name, source or risk level")
    tr.add_argument("action", choices=["list", "allow", "ask", "deny", "reset", "log"])
    tr.add_argument("target", nargs="?", help="skill name, source prefix (mcp.github, api.petstore) or read/write/danger")
    tr.add_argument("-n", type=int, default=20, help="log: number of entries")
    tr.set_defaults(func=cmd_trust)

    kb = sub.add_parser("kb", help="your files as a knowledge base: grounded, cited answers")
    kb.add_argument("action", choices=["add", "ask", "correct", "rule", "rules", "list", "remove", "export"])
    kb.add_argument("items", nargs="*", help="paths, a question, or (for correct) question and answer")
    kb.add_argument("--workspace", help="workspace folder (default ~/.libra/workspace)")
    kb.add_argument("--no-clarify", action="store_true", help="never ask a clarifying question first")
    kb.add_argument("--json", action="store_true", help="ask: print status, text, citations and questions as JSON")
    kb.set_defaults(func=cmd_kb)

    tl = sub.add_parser("translate", help="translate between languages using your own data; detect languages")
    tl.add_argument("action", choices=["text", "add", "dict", "align", "prepare", "model", "detect", "romanize", "stats"])
    tl.add_argument("items", nargs="*", help="text, or file path(s), depending on the action")
    tl.add_argument("--to", help="text: target language")
    tl.add_argument("--from", dest="from_", help="text: source language (default: detect)")
    tl.add_argument("--src", help="add/dict/align/prepare/model: source language")
    tl.add_argument("--tgt", help="add/dict/align/prepare/model: target language")
    tl.add_argument("--both", action="store_true", help="prepare: include both directions")
    tl.add_argument("--max-pairs", type=int, default=50000, help="align/prepare: sentence pairs to use")
    tl.add_argument("--iterations", type=int, default=6, help="align: EM iterations")
    tl.add_argument("--json", action="store_true", help="text: print the full result as JSON")
    tl.set_defaults(func=cmd_translate)

    w = sub.add_parser("scrape", help="extract text, tables, links or CSS-selected elements from a web page")
    w.add_argument("url")
    w.add_argument("--select", action="append", default=[], help="CSS selector (repeatable)")
    w.add_argument("--format", choices=["markdown", "text", "json"], default="markdown")
    w.add_argument("--allow-private", action="store_true", help="allow local/LAN addresses")
    w.add_argument("--ignore-robots", action="store_true", help="do not check robots.txt (only for sites you own)")
    w.set_defaults(func=cmd_scrape)

    r = sub.add_parser("crawl", help="politely crawl a website and save pages as JSONL")
    r.add_argument("url")
    r.add_argument("--max-pages", type=int, default=20)
    r.add_argument("--depth", type=int, default=2)
    r.add_argument("--delay", type=float, default=1.0, help="seconds between requests (robots.txt may ask for more)")
    r.add_argument("--include", help="only follow URLs matching this regex")
    r.add_argument("--exclude", help="skip URLs matching this regex")
    r.add_argument("--any-site", action="store_true", help="follow links to other sites too")
    r.add_argument("--out", help="write pages to this .jsonl file (default: print)")
    r.add_argument("--allow-private", action="store_true", help="allow local/LAN addresses")
    r.add_argument("--ignore-robots", action="store_true", help="do not check robots.txt (only for sites you own)")
    r.set_defaults(func=cmd_crawl)

    d = sub.add_parser("data", help="build a clean training dataset from files, web pages and books")
    d.add_argument("inputs", nargs="*", help="files or folders: .txt .md .html .json .jsonl .csv "
                   "(chat exports, ShareGPT, Alpaca and Q/A tables are detected)")
    d.add_argument("--url", action="append", default=[], help="web page or text file to download (repeatable)")
    d.add_argument("--gutenberg", action="append", type=int, default=[],
                   help="Project Gutenberg book number to download (repeatable), e.g. 1342")
    d.add_argument("--crawl", action="append", default=[], help="crawl a website and add its pages (repeatable)")
    d.add_argument("--crawl-pages", type=int, default=50)
    d.add_argument("--crawl-depth", type=int, default=2)
    d.add_argument("--crawl-delay", type=float, default=1.0)
    d.add_argument("--allow-private", action="store_true", help="allow local/LAN addresses")
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
    except (RuntimeError, FileNotFoundError, ValueError, KeyError, ImportError) as exc:
        raise SystemExit(f"libra: error: {exc}") from None


if __name__ == "__main__":
    main()
