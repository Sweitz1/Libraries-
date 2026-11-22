"""The Debian package build: packaging/build-deb.sh produces a valid, correctly-structured .deb.
Skipped where dpkg-deb isn't available (e.g. non-Debian CI)."""
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HAS_DPKG = shutil.which("dpkg-deb") is not None


@unittest.skipUnless(HAS_DPKG, "dpkg-deb not available")
class DebBuildTest(unittest.TestCase):
    def test_maintainer_scripts_are_valid_sh(self):
        for name in ("postinst", "prerm", "postrm"):
            r = subprocess.run(["sh", "-n", os.path.join(ROOT, "packaging", name)], capture_output=True)
            self.assertEqual(r.returncode, 0, name)

    def test_builds_a_valid_deb(self):
        with tempfile.TemporaryDirectory() as out:
            r = subprocess.run(["bash", os.path.join(ROOT, "packaging", "build-deb.sh"), "--out", out],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            debs = [f for f in os.listdir(out) if f.endswith(".deb")]
            self.assertEqual(len(debs), 1, debs)
            deb = os.path.join(out, debs[0])

            info = subprocess.run(["dpkg-deb", "--info", deb], capture_output=True, text=True).stdout
            self.assertIn("Package: libra", info)
            self.assertIn("Architecture: all", info)
            self.assertIn("python3-numpy", info)               # declares its runtime dependency

            contents = subprocess.run(["dpkg-deb", "--contents", deb], capture_output=True, text=True).stdout
            for path in ("./usr/bin/libra", "./usr/share/libra/libra/cli.py",
                         "./usr/share/libra/plugins/codex.py",
                         "./lib/systemd/system/libra.service",
                         "./lib/systemd/system/libra-codex.service"):
                self.assertIn(path, contents, path)

            # the control archive carries the three maintainer scripts
            ctrl = subprocess.run(["dpkg-deb", "--ctrl-tarfile", deb], capture_output=True).stdout
            names = subprocess.run(["tar", "tf", "-"], input=ctrl, capture_output=True).stdout.decode()
            for s in ("postinst", "prerm", "postrm", "control"):
                self.assertIn(s, names, s)


if __name__ == "__main__":
    unittest.main()
