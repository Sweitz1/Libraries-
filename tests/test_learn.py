"""LEARN.md and INSTALL.md stay true to the code. Every file it links exists, every `file` → `symbol` it points at
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
INSTALL = os.path.join(ROOT, "INSTALL.md")


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


def check_links(test, text: str, at_least: int) -> None:
    prose = re.sub(r"```.*?```", "", text, flags=re.S)
    links = re.findall(r"\]\(((?!https?:|#)[^)#\s]+)", prose)
    test.assertGreater(len(links), at_least)
    missing = [l for l in links if not os.path.exists(os.path.join(ROOT, l))]
    test.assertEqual(missing, [])


def check_lessons(test, text: str) -> None:
    lessons = re.findall(r"^## (\d+)\. (.+)$", text, re.M)
    test.assertGreater(len(lessons), 3)
    test.assertEqual([int(n) for n, _ in lessons], list(range(1, len(lessons) + 1)))
    for n, _ in lessons:
        test.assertIn(f"](#{n}-", text, f"lesson {n} missing from the table")
    test.assertEqual(text.count("<details><summary>Answers</summary>"), len(lessons))


def check_commands(test, text: str, at_least: int) -> None:
    from libra.cli import build_parser

    commands = []
    for block in re.findall(r"```bash\n(.*?)```", text, re.S):
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("libra "):
                continue
            # keep the command itself: drop pipes, redirects, background markers and comments
            line = re.split(r"\s(?:\||&|;|#|2>)", " " + line + " ")[0].strip()
            commands.append(shlex.split(line)[1:])
    test.assertGreater(len(commands), at_least)
    parser = build_parser()
    bad = []
    for argv in commands:
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                parser.parse_args(argv)
        except SystemExit as exc:
            if exc.code not in (0, None):  # --help exits 0; anything else is a rejected command
                bad.append(" ".join(argv))
    test.assertEqual(bad, [], "the course shows commands that libra no longer accepts")


class LearnTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(LEARN, encoding="utf-8") as f:
            cls.text = f.read()

    def test_links_resolve(self):
        check_links(self, self.text, 10)

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
        check_lessons(self, self.text)

    def test_exercise_commands_parse(self):
        check_commands(self, self.text, 10)


class InstallTest(unittest.TestCase):
    """INSTALL.md: the install course. Its links resolve, its lessons have quizzes, its `libra` commands
    are real, and the files and installer options it tells you to use exist."""

    @classmethod
    def setUpClass(cls):
        with open(INSTALL, encoding="utf-8") as f:
            cls.text = f.read()

    def test_links_resolve(self):
        check_links(self, self.text, 8)

    def test_lessons_are_listed_and_have_quizzes(self):
        check_lessons(self, self.text)

    def test_commands_parse(self):
        check_commands(self, self.text, 6)

    def test_installer_options_exist(self):
        with open(os.path.join(ROOT, "deploy", "install-debian.sh"), encoding="utf-8") as f:
            script = f.read()
        used = set(re.findall(r"install-debian\.sh((?: --[a-z-]+(?: [^\s`-][^\s`]*)?)+)", self.text))
        flags = {f for group in used for f in re.findall(r"--[a-z-]+", group)}
        flags |= set(re.findall(r"`(--[a-z-]+)", self.text.split("Useful options:")[1].split("\n\n")[0]))
        self.assertGreater(len(flags), 8)
        missing = sorted(f for f in flags if f"    {f})" not in script)
        self.assertEqual(missing, [], "INSTALL.md names installer options that don't exist")


if __name__ == "__main__":
    unittest.main()
