"""The Claude Code skills' scripts (.claude/skills/*/scripts) keep their interface: --help, clear
errors, documented exit codes, structured stdout. They are what an agent runs, so a silent change
here breaks the skill."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(ROOT, ".claude", "skills")


def run(*cmd, env=None):
    return subprocess.run(list(cmd), capture_output=True, text=True, timeout=120, env=env, cwd=ROOT)


class RollDiceTest(unittest.TestCase):
    script = os.path.join(SKILLS, "roll-dice", "scripts", "roll.py")

    def test_rolls_and_json(self):
        r = run(sys.executable, self.script, "--json", "3d8+4", "d20")
        self.assertEqual(r.returncode, 0, r.stderr)
        first, second = [json.loads(line) for line in r.stdout.splitlines()]
        self.assertEqual(len(first["rolls"]), 3)
        self.assertTrue(all(1 <= x <= 8 for x in first["rolls"]))
        self.assertEqual(first["total"], sum(first["rolls"]) + 4)
        self.assertTrue(1 <= second["total"] <= 20)
        self.assertRegex(run(sys.executable, self.script, "2d6").stdout, r"^2d6: \d \+ \d = \d+$")

    def test_errors_and_help(self):
        for bad in (["2x6"], ["0d6"], ["d1"], ["d6", "--frob"]):
            r = run(sys.executable, self.script, *bad)
            self.assertEqual(r.returncode, 2, bad)
            self.assertTrue(r.stderr.startswith("Error:"), r.stderr)
            self.assertEqual(r.stdout, "")  # nothing half-printed
        self.assertIn("Exit codes", run(sys.executable, self.script, "--help").stdout)


class VerifyScriptTest(unittest.TestCase):
    script = os.path.join(SKILLS, "verify", "scripts", "verify.sh")

    def test_interface(self):
        self.assertIn("Exit codes", run("bash", self.script, "--help").stdout)
        self.assertEqual(run("bash", self.script, "--frob").returncode, 2)
        r = run("bash", self.script, "--only", "python,nope")
        self.assertEqual(r.returncode, 2)
        self.assertIn("unknown check 'nope'", r.stderr)

    def test_a_quick_check_reports_json(self):
        r = run("bash", self.script, "--only", "xml,docs")
        lines = [json.loads(l) for l in r.stdout.splitlines()]
        self.assertEqual([l.get("check") for l in lines[:-1]], ["xml", "docs"])
        self.assertEqual(lines[-1]["summary"]["failed"], 0, r.stdout)
        self.assertEqual(r.returncode, 0)


class DeployScriptTest(unittest.TestCase):
    script = os.path.join(SKILLS, "deploy", "scripts", "deploy.sh")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        ssh = os.path.join(self.tmp.name, "fake-ssh")
        with open(ssh, "w") as f:  # a stand-in for ssh that runs commands locally
            f.write('#!/usr/bin/env bash\nwhile [ $# -gt 0 ]; do case "$1" in -o) shift 2 ;; -t) shift ;; *) break ;; esac; done\n'
                    't="$1"; shift; [ "$t" = nowhere ] && { echo "ssh: connect to host nowhere: refused" >&2; exit 255; }\n'
                    'HOME="$FAKE_HOME" bash -c "$*"\n')
        os.chmod(ssh, os.stat(ssh).st_mode | stat.S_IEXEC)
        os.makedirs(os.path.join(self.tmp.name, "home"))
        self.env = dict(os.environ, LIBRA_DEPLOY_SSH=ssh, FAKE_HOME=os.path.join(self.tmp.name, "home"))

    def tearDown(self):
        self.tmp.cleanup()

    def test_arguments_are_checked(self):
        self.assertIn("Exit codes", run("bash", self.script, "--help").stdout)
        self.assertEqual(run("bash", self.script).returncode, 2)  # --target is required
        r = run("bash", self.script, "--target", "x", "--version", "v1;reboot", env=self.env)
        self.assertEqual(r.returncode, 2)
        self.assertIn("Received: 'v1;reboot'", r.stderr)
        self.assertEqual(run("bash", self.script, "--target", "x", "--options", "--port 80 && reboot", env=self.env).returncode, 2)

    def test_unreachable_and_dry_run(self):
        r = run("bash", self.script, "--target", "nowhere", "--skip-tests", env=self.env)
        self.assertEqual(r.returncode, 3)
        self.assertEqual(json.loads(r.stdout.splitlines()[-1])["step"], "reach")
        r = run("bash", self.script, "--target", "me@server", "--version", "v0.15.0", "--options", "--tailscale",
                "--dry-run", env=self.env)
        steps = {json.loads(l)["step"]: json.loads(l) for l in r.stdout.splitlines()}
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(steps["install"]["status"], "planned")
        self.assertIn("install-debian.sh --tailscale", steps["install"]["detail"])
        self.assertEqual(os.listdir(self.env["FAKE_HOME"]), [])  # a dry run changes nothing


if __name__ == "__main__":
    unittest.main()
