"""QR codes, written from scratch: byte mode, versions 1–10, error correction L/M/Q/H.

Used to pair a phone: scan the code with the camera and the app opens signed in.

    >>> svg = to_svg(encode("https://libra.example.ts.net/#token=abc"))
"""
from __future__ import annotations

# (EC codewords per block, [(number of blocks, data codewords per block), ...]) per version and level
_BLOCKS = {
    1: {"L": (7, [(1, 19)]), "M": (10, [(1, 16)]), "Q": (13, [(1, 13)]), "H": (17, [(1, 9)])},
    2: {"L": (10, [(1, 34)]), "M": (16, [(1, 28)]), "Q": (22, [(1, 22)]), "H": (28, [(1, 16)])},
    3: {"L": (15, [(1, 55)]), "M": (26, [(1, 44)]), "Q": (18, [(2, 17)]), "H": (22, [(2, 13)])},
    4: {"L": (20, [(1, 80)]), "M": (18, [(2, 32)]), "Q": (26, [(2, 24)]), "H": (16, [(4, 9)])},
    5: {"L": (26, [(1, 108)]), "M": (24, [(2, 43)]), "Q": (18, [(2, 15), (2, 16)]), "H": (22, [(2, 11), (2, 12)])},
    6: {"L": (18, [(2, 68)]), "M": (16, [(4, 27)]), "Q": (24, [(4, 19)]), "H": (28, [(4, 15)])},
    7: {"L": (20, [(2, 78)]), "M": (18, [(4, 31)]), "Q": (18, [(2, 14), (4, 15)]), "H": (26, [(4, 13), (1, 14)])},
    8: {"L": (24, [(2, 97)]), "M": (22, [(2, 38), (2, 39)]), "Q": (22, [(4, 18), (2, 19)]),
        "H": (26, [(4, 14), (2, 15)])},
    9: {"L": (30, [(2, 116)]), "M": (22, [(3, 36), (2, 37)]), "Q": (20, [(4, 16), (4, 17)]),
        "H": (24, [(4, 12), (4, 13)])},
    10: {"L": (18, [(2, 68), (2, 69)]), "M": (26, [(4, 43), (1, 44)]), "Q": (24, [(6, 19), (2, 20)]),
         "H": (28, [(6, 15), (2, 16)])},
}
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34], 7: [6, 22, 38], 8: [6, 24, 42],
          9: [6, 26, 46], 10: [6, 28, 50]}
_EC_BITS = {"L": 1, "M": 0, "Q": 3, "H": 2}

# ---------------------------------------------------------------- GF(256)
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    return 0 if a == 0 or b == 0 else _EXP[_LOG[a] + _LOG[b]]


def _generator(n: int) -> list[int]:
    g = [1]
    for i in range(n):
        g = [a ^ _mul(b, _EXP[i]) for a, b in zip(g + [0], [0] + g)]
    return g


def _rs_remainder(data: list[int], n: int) -> list[int]:
    gen = _generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j in range(1, len(gen)):
                rem[i + j] ^= _mul(gen[j], coef)
    return rem[len(data):]


# ---------------------------------------------------------------- encoding
def _capacity(version: int, level: str) -> int:
    return sum(n * k for n, k in _BLOCKS[version][level][1])


def _codewords(data: bytes, version: int, level: str) -> list[int]:
    count_bits = 8 if version < 10 else 16
    bits = [0, 1, 0, 0]  # byte mode
    bits += [(len(data) >> i) & 1 for i in range(count_bits - 1, -1, -1)]
    for byte in data:
        bits += [(byte >> i) & 1 for i in range(7, -1, -1)]
    total = _capacity(version, level) * 8
    bits += [0] * min(4, total - len(bits))
    bits += [0] * (-len(bits) % 8)
    words = [int("".join(map(str, bits[i : i + 8])), 2) for i in range(0, len(bits), 8)]
    pad = (0xEC, 0x11)
    while len(words) < total // 8:
        words.append(pad[(len(words) - len(bits) // 8) % 2])
    # split into blocks, add error correction, interleave
    ec_len, groups = _BLOCKS[version][level]
    blocks, pos = [], 0
    for n, k in groups:
        for _ in range(n):
            blocks.append(words[pos : pos + k])
            pos += k
    ecs = [_rs_remainder(b, ec_len) for b in blocks]
    out = []
    for i in range(max(len(b) for b in blocks)):
        out += [b[i] for b in blocks if i < len(b)]
    for i in range(ec_len):
        out += [e[i] for e in ecs]
    return out


def _bch(value: int, poly: int, bits: int) -> int:
    v = value << bits
    while v.bit_length() > bits:
        v ^= poly << (v.bit_length() - poly.bit_length())
    return (value << bits) | v


class _Matrix:
    def __init__(self, version: int):
        self.v = version
        self.n = 17 + 4 * version
        self.dark = [[False] * self.n for _ in range(self.n)]
        self.fixed = [[False] * self.n for _ in range(self.n)]

    def set(self, r: int, c: int, dark: bool) -> None:
        self.dark[r][c] = dark
        self.fixed[r][c] = True

    def functional(self) -> None:
        n = self.n
        for r0, c0 in ((0, 0), (0, n - 7), (n - 7, 0)):  # finders with separators
            for r in range(-1, 8):
                for c in range(-1, 8):
                    rr, cc = r0 + r, c0 + c
                    if 0 <= rr < n and 0 <= cc < n:
                        ring = max(abs(r - 3), abs(c - 3))
                        self.set(rr, cc, ring in (0, 1, 3) if ring <= 3 else False)
        for i in range(8, n - 8):  # timing
            self.set(6, i, i % 2 == 0)
            self.set(i, 6, i % 2 == 0)
        pos = _ALIGN[self.v]
        last = len(pos) - 1
        for i, r in enumerate(pos):
            for j, c in enumerate(pos):
                if (i, j) in ((0, 0), (0, last), (last, 0)):
                    continue  # these three would sit on the finder patterns
                for dr in range(-2, 3):
                    for dc in range(-2, 3):
                        self.set(r + dr, c + dc, max(abs(dr), abs(dc)) != 1)
        self.set(n - 8, 8, True)  # the dark module
        for i in range(9):  # reserve format areas
            if not self.fixed[8][i]:
                self.set(8, i, False)
            if not self.fixed[i][8]:
                self.set(i, 8, False)
        for i in range(8):
            self.set(8, n - 1 - i, False)
            self.set(n - 1 - i, 8, False)
        if self.v >= 7:
            info = _bch(self.v, 0x1F25, 12)
            for i in range(18):
                bit = bool((info >> i) & 1)
                r, c = i // 3, i % 3 + n - 11
                self.set(r, c, bit)
                self.set(c, r, bit)

    def place(self, words: list[int]) -> None:
        bits = [(w >> i) & 1 for w in words for i in range(7, -1, -1)]
        i, n = 0, self.n
        col, upward = n - 1, True
        while col > 0:
            if col == 6:
                col -= 1
            rows = range(n - 1, -1, -1) if upward else range(n)
            for r in rows:
                for c in (col, col - 1):
                    if not self.fixed[r][c]:
                        self.dark[r][c] = bool(bits[i]) if i < len(bits) else False
                        i += 1
            upward = not upward
            col -= 2

    def format_bits(self, level: str, mask: int) -> None:
        n = self.n
        info = _bch((_EC_BITS[level] << 3) | mask, 0x537, 10) ^ 0x5412
        bit = [bool((info >> i) & 1) for i in range(15)]
        for i in range(6):
            self.dark[i][8] = bit[i]
        self.dark[7][8] = bit[6]
        self.dark[8][8] = bit[7]
        self.dark[8][7] = bit[8]
        for i in range(9, 15):
            self.dark[8][14 - i] = bit[i]
        for i in range(8):
            self.dark[8][n - 1 - i] = bit[i]
        for i in range(8, 15):
            self.dark[n - 15 + i][8] = bit[i]
        self.dark[n - 8][8] = True


_MASKS = [
    lambda r, c: (r + c) % 2 == 0, lambda r, c: r % 2 == 0, lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0, lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0, lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _penalty(m: list[list[bool]]) -> int:
    n, score = len(m), 0
    for lines in (m, [list(col) for col in zip(*m)]):
        for line in lines:
            run, prev = 0, None
            for v in line:
                run = run + 1 if v == prev else 1
                prev = v
                if run == 5:
                    score += 3
                elif run > 5:
                    score += 1
            s = "".join("1" if v else "0" for v in line)
            score += 40 * (s.count("10111010000") + s.count("00001011101"))
    for r in range(n - 1):
        for c in range(n - 1):
            if m[r][c] == m[r][c + 1] == m[r + 1][c] == m[r + 1][c + 1]:
                score += 3
    dark = sum(map(sum, m))
    score += 10 * (abs(dark * 20 - n * n * 10) // (n * n))
    return score


def encode(text: str, level: str = "M") -> list[list[bool]]:
    """QR matrix (True = dark module) for ``text``, using the smallest version that fits."""
    data = text.encode("utf-8")
    for version in range(1, 11):
        header = 4 + (8 if version < 10 else 16)
        if (header + 8 * len(data) + 7) // 8 <= _capacity(version, level):
            break
    else:
        raise ValueError(f"text too long for a version-10 QR code ({len(data)} bytes)")
    words = _codewords(data, version, level)
    best, best_score = None, None
    for mask in range(8):
        mat = _Matrix(version)
        mat.functional()
        mat.place(words)
        fn = _MASKS[mask]
        for r in range(mat.n):
            for c in range(mat.n):
                if not mat.fixed[r][c] and fn(r, c):
                    mat.dark[r][c] = not mat.dark[r][c]
        mat.format_bits(level, mask)
        score = _penalty(mat.dark)
        if best_score is None or score < best_score:
            best, best_score = mat.dark, score
    return best


def to_svg(matrix: list[list[bool]], scale: int = 8, border: int = 4) -> str:
    n = len(matrix)
    size = (n + 2 * border) * scale
    path = "".join(f"M{(c + border) * scale},{(r + border) * scale}h{scale}v{scale}h-{scale}z"
                   for r in range(n) for c in range(n) if matrix[r][c])
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {size} {size}" width="{size}" height="{size}" '
            f'shape-rendering="crispEdges"><rect width="100%" height="100%" fill="#fff"/>'
            f'<path d="{path}" fill="#000"/></svg>')


def to_terminal(matrix: list[list[bool]], border: int = 2) -> str:
    """Two rows per text line with half blocks; black on white, so it scans on dark terminals too."""
    n = len(matrix)
    size = n + 2 * border

    def dark(r, c):
        return 0 <= r - border < n and 0 <= c - border < n and matrix[r - border][c - border]

    lines = []
    for r in range(0, size, 2):
        chars = []
        for c in range(size):
            top, bottom = dark(r, c), dark(r + 1, c)
            chars.append({(False, False): " ", (True, False): "▀", (False, True): "▄", (True, True): "█"}[(top, bottom)])
        lines.append("\x1b[30;47m" + "".join(chars) + "\x1b[0m")
    return "\n".join(lines)
