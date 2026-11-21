"""LEARN.md stays true to the code. Every file it links exists, every `file` → `symbol` it points at
is still defined there, and every `libra …` command in its exercises still parses. A rename that
would make the course lie fails here instead."""
import ast
import contextlib
import io
import os
import re
import shlex
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEARN = os.path.join(ROOT, "LEARN.md")


def _definitions(path: str) -> set:
    """Top-level functions, classes and assignments, plus Class.method and Class.attribute."""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    names = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
            if isinstance(node, ast.ClassDef):
                for item in node.body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        names.add(f"{node.name}.{item.name}")
                    elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                        for t in (item.targets if isinstance(item, ast.Assign) else [item.target]):
                            if isinstance(t, ast.Name):
                                names.add(f"{node.name}.{t.id}")
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                if isinstance(t, ast.Name):
                    names.add(t.id)
    return names


class LearnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(LEARN, encoding="utf-8") as f:
            cls.text = f.read()

    def test_links_resolve(self):
        prose = re.sub(r"```.*?```", "", self.text, flags=re.S)
        links = re.findall(r"\]\(((?!https?:|#)[^)#\s]+)", prose)
        self.assertGreater(len(links), 10)
        missing = [l for l in links if not os.path.exists(os.path.join(ROOT, l))]
        self.assertEqual(missing, [])

    def test_key_code_exists(self):
        refs = re.findall(r"`([\w/.-]+\.py)` → `([\w.]+)`", self.text)
        self.assertGreater(len(refs), 40)
        gone = []
        cache = {}
        for path, symbol in refs:
            full = os.path.join(ROOT, path)
            if not os.path.exists(full):
                gone.append(f"{path} (file)")
                continue
            if path not in cache:
                cache[path] = _definitions(full)
            if symbol not in cache[path]:
                gone.append(f"{path} → {symbol}")
        self.assertEqual(gone, [], "LEARN.md points at code that no longer exists")

    def test_lessons_are_listed_and_have_quizzes(self):
        lessons = re.findall(r"^## (\d+)\. (.+)$", self.text, re.M)
        self.assertEqual([int(n) for n, _ in lessons], list(range(1, len(lessons) + 1)))
        for n, _ in lessons:
            self.assertIn(f"](#{n}-", self.text, f"lesson {n} missing from the table")
        self.assertEqual(self.text.count("<details><summary>Answers</summary>"), len(lessons))

    def test_exercise_commands_parse(self):
        from libra.cli import build_parser

        commands = []
        for block in re.findall(r"```bash\n(.*?)```", self.text, re.S):
            for line in block.splitlines():
                line = line.strip()
                if not line.startswith("libra "):
                    continue
                # keep the command itself: drop pipes, redirects, background markers and comments
                line = re.split(r"\s(?:\||&|;|#|2>)", " " + line + " ")[0].strip()
                commands.append(shlex.split(line)[1:])
        self.assertGreater(len(commands), 10)
        parser = build_parser()
        bad = []
        for argv in commands:
            try:
                with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                    parser.parse_args(argv)
            except SystemExit as exc:
                if exc.code not in (0, None):  # --help exits 0; anything else is a rejected command
                    bad.append(" ".join(argv))
        self.assertEqual(bad, [], "LEARN.md shows commands that libra no longer accepts")


if __name__ == "__main__":
    unittest.main()
