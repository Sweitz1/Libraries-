import functools
import json
import os
import random
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

WORDS = ("the river runs past the old mill where children learn to read and write stories about long summer days "
         "while farmers bring bread and apples to the market near the quiet library").split()


def para(seed, sentences=6):
    rnd = random.Random(seed)
    return " ".join(" ".join(rnd.choice(WORDS) for _ in range(14)).capitalize() + "." for _ in range(sentences))


def site(folder):
    pages = {f"a{i}.html": f"<html><head><title>Article {i}</title></head><body><nav>Home</nav><main><h1>Story {i}</h1>"
                           f"<p>{para(i)}</p><p>{para(i + 50)}</p><p>Share on Facebook</p></main>"
                           f"<footer>All rights reserved</footer></body></html>" for i in range(1, 6)}
    pages.update({
        "dup.html": pages["a1.html"],
        "fr.html": "<html><body><main><p>" + "Le chat est sur la table et il dort très bien dans la maison. " * 20
                   + "</p></main></body></html>",
        "junk.html": "<html><body><main><p>" + "{{ $$ ## 1234 % ;; }} " * 60 + "</p></main></body></html>",
        "contact.html": f"<html><body><main><p>{para(99)} Write to mira@example.com or call +1 555 123 4567.</p>"
                        "</main></body></html>",
        "private/secret.html": f"<html><body><main><p>{para(7)}</p></main></body></html>",
        "robots.txt": "User-agent: *\nDisallow: /private/\n",
    })
    links = "".join(f"<a href='{n}'>{n}</a>" for n in pages if n.endswith(".html"))
    pages["index.html"] = f"<html><body>{links}</body></html>"
    for name, body in pages.items():
        path = os.path.join(folder, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(body)


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


class FilterTest(unittest.TestCase):
    def test_clean_check_mask(self):
        from libra.auto import TEMPLATE, check, clean, mask_personal

        text = clean("Accept all cookies\nStory\n\n" + para(1) + "\nok\nSign in\n")
        self.assertTrue(text.startswith("Story\n\n"))  # heading kept, banner and fragments dropped
        self.assertNotIn("cookies", text)
        f = dict(TEMPLATE["filters"], min_chars=100)
        self.assertIsNone(check(para(1), f))
        self.assertEqual(check("short", f), "too short")
        self.assertEqual(check("{{ $$ ## 12 }} " * 30, f), "mostly symbols or numbers")
        self.assertEqual(check("Le chat est sur la table et il dort bien. " * 10, f), "wrong language (fr)")
        self.assertEqual(check("same line here\n" * 10, dict(f, min_chars=10, language="")), "repetitive")
        masked, n = mask_personal("mail a.b@x.org or +44 20 7946 0958, born 1990-2000")
        self.assertEqual((masked, n), ("mail [email] or [phone], born 1990-2000", 2))


class AutoRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.old = os.environ.get("LIBRA_HOME")
        os.environ["LIBRA_HOME"] = os.path.join(self.tmp.name, "home")
        self.www = os.path.join(self.tmp.name, "www")
        os.makedirs(self.www)
        site(self.www)
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=self.www))
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{self.server.server_address[1]}"
        with open(os.path.join(self.www, "feed.xml"), "w") as f:
            f.write(f'<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                    f'<link href="{base}/a5.html"/></entry></feed>')
        from libra.auto import TEMPLATE

        cfg = json.loads(json.dumps(TEMPLATE))
        cfg.update(sources=[{"crawl": base + "/", "max_pages": 30, "depth": 1}, {"feed": base + "/feed.xml"}],
                   allow_private=True, steps_per_run=20, device="cpu")
        cfg["limits"].update(delay_seconds=0, min_free_disk_mb=1)
        cfg["model_config"].update(layers=1, embd=32, heads=2, ctx=32, vocab=400, batch=8)
        cfg["filters"]["min_chars"] = 200
        cfg["gate"]["holdout_fraction"] = 0.25
        self.cfg_path = os.path.join(self.tmp.name, "auto.json")
        self.write(cfg)
        self.cfg = cfg

    def write(self, cfg):
        with open(self.cfg_path, "w") as f:
            json.dump(cfg, f)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        if self.old is None:
            os.environ.pop("LIBRA_HOME", None)
        else:
            os.environ["LIBRA_HOME"] = self.old
        self.tmp.cleanup()

    def test_full_cycle(self):
        from libra import auto

        dry = auto.run(self.cfg_path, dry_run=True, log=None)
        self.assertEqual(dry.decision, "dry run: nothing written or trained")
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "checkpoints")))

        r1 = auto.run(self.cfg_path, log=None)
        self.assertEqual(r1.kept, 6)  # a1..a5 and contact; dup, French, junk and index are dropped
        # dup.html never gets this far: the crawler already skips pages with identical content
        self.assertEqual(r1.dropped, {"wrong language (fr)": 1, "mostly symbols or numbers": 1, "too short": 1})
        self.assertEqual(r1.masked, 2)
        self.assertEqual(r1.decision, "kept the new model")
        data = os.path.join(self.tmp.name, "datasets", "auto")
        stored = "".join(open(os.path.join(root, f)).read() for root, _, fs in os.walk(data) for f in fs
                         if f.endswith(".jsonl"))
        self.assertNotIn("mira@example.com", stored)
        self.assertNotIn("Share on Facebook", stored)
        self.assertNotIn(para(7)[:40], stored)  # robots.txt-disallowed page never fetched

        r2 = auto.run(self.cfg_path, log=None)
        self.assertEqual((r2.kept, r2.unchanged), (0, r2.fetched))
        self.assertIn("no new text", r2.decision)

        with open(os.path.join(self.www, "a9.html"), "w") as f:
            f.write(f"<html><body><main><p>{para(9)} {para(19)}</p></main></body></html>")
        with open(os.path.join(self.www, "index.html"), "a") as f:
            f.write("<a href='a9.html'>9</a>")
        self.cfg["gate"]["max_regression"] = -0.99  # demand a 99% improvement: the gate must say no
        self.write(self.cfg)
        r3 = auto.run(self.cfg_path, log=None)
        self.assertTrue(r3.decision.startswith("kept the old model"), r3.decision)
        model = os.path.join(self.tmp.name, "checkpoints", "auto")
        self.assertTrue(os.path.exists(model + ".rejected"))
        self.assertTrue(auto.status(self.cfg_path).startswith("3 run(s), 1 improved the model"))

        self.cfg["gate"]["max_regression"] = 0.5
        self.write(self.cfg)
        for n in (10, 11, 12):  # several pages, since one in four kept documents goes to the held-out set
            with open(os.path.join(self.www, f"a{n}.html"), "w") as f:
                f.write(f"<html><body><main><p>{para(n)} {para(n + 20)}</p></main></body></html>")
            with open(os.path.join(self.www, "index.html"), "a") as f:
                f.write(f"<a href='a{n}.html'>{n}</a>")
        r4 = auto.run(self.cfg_path, log=None)
        self.assertTrue(r4.decision.startswith("kept the new model"), r4.decision)
        self.assertGreater(r4.steps, 0)
        self.assertTrue(os.path.exists(model + ".previous"))
        auto.rollback(self.cfg_path)
        self.assertTrue(os.path.exists(os.path.join(model, "model.npz")))

    def test_lock_and_time_limit_and_schedule(self):
        from libra import auto

        lock = os.path.join(self.tmp.name, "datasets", "auto", ".auto")
        os.makedirs(lock, exist_ok=True)
        with open(os.path.join(lock, "lock"), "w") as f:
            f.write(str(os.getpid()))
        with self.assertRaises(RuntimeError):
            auto.run(self.cfg_path, log=None)
        os.remove(os.path.join(lock, "lock"))
        with open(os.path.join(lock, "lock"), "w") as f:
            f.write("999999999")  # stale lock from a crashed run: ignored
        self.cfg["limits"]["max_minutes"] = 0.1  # 6 s: too little time to start training
        self.write(self.cfg)
        r = auto.run(self.cfg_path, log=None)
        self.assertEqual(r.decision, "time limit reached before training")
        lines = auto.schedule_lines(self.cfg_path, "03:15")
        self.assertTrue(lines["cron"].startswith("15 3 * * * "))
        self.assertIn("/ST 03:15", lines["windows"])
        with self.assertRaises(ValueError):
            auto.schedule_lines(self.cfg_path, "25:00")


if __name__ == "__main__":
    unittest.main()
