"""libra.raster: from-scratch PNG writer + bitmap font. Decode the PNG back (zlib) to check pixels."""
import struct
import unittest
import zlib

from libra.raster import Canvas


def decode_png(data: bytes):
    """Minimal PNG reader for our own output: returns (w, h, rgba bytearray)."""
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    i, w, h, idat = 8, 0, 0, b""
    while i < len(data):
        (ln,) = struct.unpack(">I", data[i:i + 4])
        tag = data[i + 4:i + 8]
        body = data[i + 8:i + 8 + ln]
        if tag == b"IHDR":
            w, h = struct.unpack(">II", body[:8])
        elif tag == b"IDAT":
            idat += body
        i += 12 + ln
    raw = zlib.decompress(idat)
    stride = w * 4
    out = bytearray()
    for y in range(h):
        out += raw[y * (stride + 1) + 1:y * (stride + 1) + 1 + stride]   # drop the filter byte
    return w, h, out


class RasterTest(unittest.TestCase):
    def px(self, buf, w, x, y):
        i = (y * w + x) * 4
        return tuple(buf[i:i + 3])

    def test_png_roundtrips_dimensions_and_background(self):
        c = Canvas(20, 10, (255, 0, 0))
        w, h, buf = decode_png(c.to_png())
        self.assertEqual((w, h), (20, 10))
        self.assertEqual(self.px(buf, w, 5, 5), (255, 0, 0))

    def test_fill_rect_and_clipping(self):
        c = Canvas(10, 10, (0, 0, 0))
        c.fill_rect(2, 2, 3, 3, (0, 255, 0))
        c.fill_rect(8, 8, 50, 50, (0, 0, 255))                  # runs off the edge, must not crash
        w, h, buf = decode_png(c.to_png())
        self.assertEqual(self.px(buf, w, 3, 3), (0, 255, 0))
        self.assertEqual(self.px(buf, w, 0, 0), (0, 0, 0))
        self.assertEqual(self.px(buf, w, 9, 9), (0, 0, 255))

    def test_alpha_blend(self):
        c = Canvas(4, 4, (0, 0, 0))
        c.fill_rect(0, 0, 4, 4, (255, 255, 255), alpha=128)     # ~50% white over black
        _, _, buf = decode_png(c.to_png())
        self.assertTrue(100 <= buf[0] <= 140)

    def test_text_draws_pixels_and_measures(self):
        c = Canvas(120, 20, (0, 0, 0))
        c.text(2, 2, "HI", (255, 255, 255), scale=2)
        w, h, buf = decode_png(c.to_png())
        lit = sum(1 for y in range(h) for x in range(w) if self.px(buf, w, x, y) != (0, 0, 0))
        self.assertGreater(lit, 20)                             # the glyphs put ink on the canvas
        self.assertEqual(Canvas.text_width("HI", 2), 2 * 6 * 2)
        self.assertEqual(Canvas.line_height(2), 18)

    def test_unknown_char_does_not_crash(self):
        c = Canvas(40, 16, (255, 255, 255))
        c.text(1, 1, "☢ é", (0, 0, 0), scale=1)       # radioactive sign + é → fallbacks
        self.assertEqual(decode_png(c.to_png())[:2], (40, 16))


if __name__ == "__main__":
    unittest.main()
