import random
import string
import unittest

from libra import qr

try:  # an independent decoder, only used when installed (CI installs it)
    import cv2
    import numpy as np
except ImportError:  # pragma: no cover
    cv2 = None


def image(m, scale=6, border=4):
    n = len(m)
    img = np.full(((n + 2 * border) * scale,) * 2, 255, np.uint8)
    for r in range(n):
        for c in range(n):
            if m[r][c]:
                img[(r + border) * scale:(r + border + 1) * scale, (c + border) * scale:(c + border + 1) * scale] = 0
    return img


class QRTest(unittest.TestCase):
    def test_structure(self):
        m = qr.encode("hello")
        self.assertEqual(len(m), 21)  # version 1
        for r0, c0 in ((0, 0), (0, 14), (14, 0)):  # the three finder patterns
            self.assertTrue(all(m[r0][c0 + i] for i in range(7)))
            self.assertTrue(m[r0 + 3][c0 + 3])
            self.assertFalse(m[r0 + 1][c0 + 1])
        self.assertEqual([m[6][c] for c in range(8, 13)], [True, False, True, False, True])  # timing
        self.assertTrue(m[21 - 8][8])  # the dark module
        self.assertEqual(len(qr.encode("x" * 120)), 17 + 4 * 7)  # grows to fit
        with self.assertRaises(ValueError):
            qr.encode("x" * 400)
        svg = qr.to_svg(m)
        self.assertTrue(svg.startswith("<svg") and "<path" in svg)
        self.assertIn("▀", qr.to_terminal(m))

    @unittest.skipIf(cv2 is None, "OpenCV not installed")
    def test_decodes_with_an_independent_reader(self):
        detectors = [cv2.QRCodeDetector(), cv2.QRCodeDetectorAruco()]
        rnd = random.Random(3)
        cases = ["https://libra.tail1234.ts.net/#token=zC8DMFqXNfuL9E-qrSBieW4OjMlO7cQ7", "héllo wörld ✓"]
        cases += ["".join(rnd.choice(string.ascii_letters + string.digits + "/:#.-_") for _ in range(n))
                  for n in (1, 14, 30, 60, 90, 120, 150, 180, 200)]
        for level in "LMQH":
            for text in cases:
                try:
                    m = qr.encode(text, level)
                except ValueError:
                    continue
                got = next((d.detectAndDecode(image(m, s))[0] for d in detectors for s in (6, 8, 10)
                            if d.detectAndDecode(image(m, s))[0]), "")
                self.assertEqual(got, text, f"level {level}, {len(text)} chars")


if __name__ == "__main__":
    unittest.main()
