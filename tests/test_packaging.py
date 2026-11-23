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
                         "./lib/systemd/system/libra-codex.service",
                         "./lib/systemd/system/libra-discord.service",
                         # desktop integration: menu launchers + themed app icon
                         "./usr/share/applications/libra.desktop",
                         "./usr/share/applications/libra-codex.desktop",
                         "./usr/share/icons/hicolor/512x512/apps/libra.png",
                         "./usr/share/icons/hicolor/scalable/apps/libra.svg"):
                self.assertIn(path, contents, path)

            # the control archive carries the three maintainer scripts
            ctrl = subprocess.run(["dpkg-deb", "--ctrl-tarfile", deb], capture_output=True).stdout
            names = subprocess.run(["tar", "tf", "-"], input=ctrl, capture_output=True).stdout.decode()
            for s in ("postinst", "prerm", "postrm", "control"):
                self.assertIn(s, names, s)

    @unittest.skipUnless(shutil.which("dpkg-scanpackages"), "dpkg-scanpackages not available")
    def test_builds_an_apt_repo(self):
        with tempfile.TemporaryDirectory() as work:
            deb_dir = os.path.join(work, "deb")
            r = subprocess.run(["bash", os.path.join(ROOT, "packaging", "build-deb.sh"), "--out", deb_dir],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            repo = os.path.join(work, "apt")
            r = subprocess.run(["bash", os.path.join(ROOT, "packaging", "build-apt-repo.sh"), "--out", repo,
                                "--pool", deb_dir], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            # the apt metadata and the pooled package exist
            self.assertTrue(os.path.isfile(os.path.join(repo, "dists", "stable", "Release")))
            self.assertTrue(os.path.isfile(os.path.join(repo, "dists", "stable", "main", "binary-all", "Packages")))
            self.assertTrue(os.path.isfile(os.path.join(repo, "index.html")))
            deb_name = os.path.basename([f for f in os.listdir(deb_dir) if f.endswith(".deb")][0])
            self.assertTrue(os.path.isfile(os.path.join(repo, "pool", "main", deb_name)))
            # the Packages index names the package and points at the pool file
            with open(os.path.join(repo, "dists", "stable", "main", "binary-all", "Packages")) as f:
                pkgs = f.read()
            self.assertIn("Package: libra", pkgs)
            self.assertIn("Filename: pool/main/" + deb_name, pkgs)
            # the Release lists the Packages hashes
            with open(os.path.join(repo, "dists", "stable", "Release")) as f:
                rel = f.read()
            self.assertIn("SHA256:", rel)
            self.assertIn("main/binary-all/Packages", rel)


if __name__ == "__main__":
    unittest.main()
