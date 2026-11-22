"""A tiny raster image: draw filled rectangles and bitmap text, and write a PNG — standard library only.

Libra has no image library (no PIL/Pillow), so when a PNG is wanted (for a card, a chart, an icon) this
renders one from scratch: an RGBA pixel buffer with alpha blending, a built-in 5x7 bitmap font, and a PNG
encoder (zlib-compressed scanlines with CRC32 chunks, per the PNG spec). The font is drawn in small-caps
(one glyph set, used for upper and lower case), which suits a card or label.

    c = Canvas(200, 60, (255, 255, 255))
    c.fill_rect(0, 0, 200, 20, (43, 58, 87))
    c.text(6, 6, "LIBRA", (255, 255, 255), scale=2)
    open("out.png", "wb").write(c.to_png())
"""
from __future__ import annotations

import struct
import zlib

# 5x7 bitmap font, authored as ASCII art so each glyph is verifiable by eye. One case (small-caps).
_GLYPHS = {
    " ": ["     ", "     ", "     ", "     ", "     ", "     ", "     "],
    "A": [" ### ", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
    "B": ["#### ", "#   #", "#### ", "#   #", "#   #", "#   #", "#### "],
    "C": [" ####", "#    ", "#    ", "#    ", "#    ", "#    ", " ####"],
    "D": ["#### ", "#   #", "#   #", "#   #", "#   #", "#   #", "#### "],
    "E": ["#####", "#    ", "#### ", "#    ", "#    ", "#    ", "#####"],
    "F": ["#####", "#    ", "#### ", "#    ", "#    ", "#    ", "#    "],
    "G": [" ####", "#    ", "#    ", "#  ##", "#   #", "#   #", " ####"],
    "H": ["#   #", "#   #", "#   #", "#####", "#   #", "#   #", "#   #"],
    "I": [" ### ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    "J": ["  ###", "   # ", "   # ", "   # ", "#  # ", "#  # ", " ##  "],
    "K": ["#   #", "#  # ", "# #  ", "##   ", "# #  ", "#  # ", "#   #"],
    "L": ["#    ", "#    ", "#    ", "#    ", "#    ", "#    ", "#####"],
    "M": ["#   #", "## ##", "# # #", "# # #", "#   #", "#   #", "#   #"],
    "N": ["#   #", "##  #", "# # #", "# # #", "#  ##", "#   #", "#   #"],
    "O": [" ### ", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    "P": ["#### ", "#   #", "#   #", "#### ", "#    ", "#    ", "#    "],
    "Q": [" ### ", "#   #", "#   #", "#   #", "# # #", "#  # ", " ## #"],
    "R": ["#### ", "#   #", "#   #", "#### ", "# #  ", "#  # ", "#   #"],
    "S": [" ####", "#    ", "#    ", " ### ", "    #", "    #", "#### "],
    "T": ["#####", "  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "  #  "],
    "U": ["#   #", "#   #", "#   #", "#   #", "#   #", "#   #", " ### "],
    "V": ["#   #", "#   #", "#   #", "#   #", "#   #", " # # ", "  #  "],
    "W": ["#   #", "#   #", "#   #", "# # #", "# # #", "## ##", "#   #"],
    "X": ["#   #", "#   #", " # # ", "  #  ", " # # ", "#   #", "#   #"],
    "Y": ["#   #", "#   #", " # # ", "  #  ", "  #  ", "  #  ", "  #  "],
    "Z": ["#####", "    #", "   # ", "  #  ", " #   ", "#    ", "#####"],
    "0": [" ### ", "#   #", "#  ##", "# # #", "##  #", "#   #", " ### "],
    "1": ["  #  ", " ##  ", "  #  ", "  #  ", "  #  ", "  #  ", " ### "],
    "2": [" ### ", "#   #", "    #", "   # ", "  #  ", " #   ", "#####"],
    "3": ["#####", "   # ", "  #  ", "   # ", "    #", "#   #", " ### "],
    "4": ["   # ", "  ## ", " # # ", "#  # ", "#####", "   # ", "   # "],
    "5": ["#####", "#    ", "#### ", "    #", "    #", "#   #", " ### "],
    "6": ["  ## ", " #   ", "#    ", "#### ", "#   #", "#   #", " ### "],
    "7": ["#####", "    #", "   # ", "  #  ", " #   ", " #   ", " #   "],
    "8": [" ### ", "#   #", "#   #", " ### ", "#   #", "#   #", " ### "],
    "9": [" ### ", "#   #", "#   #", " ####", "    #", "   # ", " ##  "],
    ".": ["     ", "     ", "     ", "     ", "     ", " ##  ", " ##  "],
    ",": ["     ", "     ", "     ", "     ", " ##  ", " ##  ", "#    "],
    ":": ["     ", " ##  ", " ##  ", "     ", " ##  ", " ##  ", "     "],
    ";": ["     ", " ##  ", " ##  ", "     ", " ##  ", " ##  ", "#    "],
    "-": ["     ", "     ", "     ", "#####", "     ", "     ", "     "],
    "+": ["     ", "  #  ", "  #  ", "#####", "  #  ", "  #  ", "     "],
    "/": ["    #", "    #", "   # ", "  #  ", " #   ", "#    ", "#    "],
    "(": ["   # ", "  #  ", " #   ", " #   ", " #   ", "  #  ", "   # "],
    ")": [" #   ", "  #  ", "   # ", "   # ", "   # ", "  #  ", " #   "],
    "[": [" ### ", " #   ", " #   ", " #   ", " #   ", " #   ", " ### "],
    "]": [" ### ", "   # ", "   # ", "   # ", "   # ", "   # ", " ### "],
    "'": ["  #  ", "  #  ", " #   ", "     ", "     ", "     ", "     "],
    "\"": ["# #  ", "# #  ", "# #  ", "     ", "     ", "     ", "     "],
    "!": ["  #  ", "  #  ", "  #  ", "  #  ", "  #  ", "     ", "  #  "],
    "?": [" ### ", "#   #", "   # ", "  #  ", "  #  ", "     ", "  #  "],
    "#": [" # # ", " # # ", "#####", " # # ", "#####", " # # ", " # # "],
    "%": ["##  #", "##  #", "   # ", "  #  ", " #   ", "#  ##", "#  ##"],
    "&": [" ##  ", "#  # ", "#  # ", " ##  ", "# # #", "#  # ", " ## #"],
    "*": ["     ", " # # ", " ### ", "#####", " ### ", " # # ", "     "],
    "=": ["     ", "     ", "#####", "     ", "#####", "     ", "     "],
    "x": ["     ", "     ", "#   #", " # # ", "  #  ", " # # ", "#   #"],
    "_": ["     ", "     ", "     ", "     ", "     ", "     ", "#####"],
    "@": [" ### ", "#   #", "# ###", "# # #", "# ###", "#    ", " ### "],
}
GLYPH_W, GLYPH_H = 5, 7
_UNKNOWN = ["#####", "#   #", "#   #", "#   #", "#   #", "#   #", "#####"]
# Fancy characters normalised to glyphs we have.
_NORMALISE = {"×": "x", "–": "-", "—": "-", "…": "...", "’": "'", "‘": "'", "“": "\"", "”": "\"",
              "•": "*", "·": ".", "→": "->", "™": "(tm)", "é": "e", "ë": "e", "ü": "u", "ö": "o", "ñ": "n"}


def _norm(text: str) -> str:
    return "".join(_NORMALISE.get(ch, ch) for ch in text)


class Canvas:
    def __init__(self, width: int, height: int, bg=(255, 255, 255)):
        self.w, self.h = int(width), int(height)
        r, g, b = bg
        self.buf = bytearray([r, g, b, 255] * (self.w * self.h))

    def _blend(self, x: int, y: int, color, alpha: int):
        if not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = (y * self.w + x) * 4
        if alpha >= 255:
            self.buf[i:i + 3] = bytes(color)
            self.buf[i + 3] = 255
            return
        a = alpha / 255.0
        for k in range(3):
            self.buf[i + k] = int(color[k] * a + self.buf[i + k] * (1 - a))

    def fill_rect(self, x: int, y: int, w: int, h: int, color, alpha: int = 255):
        x, y, w, h = int(x), int(y), int(w), int(h)
        x0, y0, x1, y1 = max(0, x), max(0, y), min(self.w, x + w), min(self.h, y + h)
        if alpha >= 255:                                         # fast path: opaque row fill
            row = bytes(list(color) + [255]) * max(0, x1 - x0)
            for yy in range(y0, y1):
                i = (yy * self.w + x0) * 4
                self.buf[i:i + len(row)] = row
        else:
            for yy in range(y0, y1):
                for xx in range(x0, x1):
                    self._blend(xx, yy, color, alpha)

    @staticmethod
    def text_width(text: str, scale: int = 1) -> int:
        return len(_norm(text)) * (GLYPH_W + 1) * scale

    @staticmethod
    def line_height(scale: int = 1) -> int:
        return (GLYPH_H + 2) * scale

    def text(self, x: int, y: int, text: str, color, scale: int = 1):
        cx = int(x)
        for ch in _norm(text):
            glyph = _GLYPHS.get(ch) or _GLYPHS.get(ch.upper()) or _UNKNOWN
            for ry, row in enumerate(glyph):
                for rx, cell in enumerate(row):
                    if cell != " ":
                        self.fill_rect(cx + rx * scale, int(y) + ry * scale, scale, scale, color)
            cx += (GLYPH_W + 1) * scale

    def to_png(self) -> bytes:
        stride = self.w * 4
        raw = bytearray()
        for yy in range(self.h):                                 # filter byte 0 (None) per scanline
            raw.append(0)
            raw += self.buf[yy * stride:(yy + 1) * stride]
        comp = zlib.compress(bytes(raw), 9)

        def chunk(tag: bytes, data: bytes) -> bytes:
            return (struct.pack(">I", len(data)) + tag + data
                    + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

        ihdr = struct.pack(">IIBBBBB", self.w, self.h, 8, 6, 0, 0, 0)  # 8-bit RGBA
        return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
                + chunk(b"IDAT", comp) + chunk(b"IEND", b""))
