"""Byte-level BPE tokenizer trained from your own text."""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict

# Pre-tokenisation: words with a leading space, numbers (max 3 digits),
# punctuation runs and whitespace. Merges never cross these boundaries.
PATTERN = re.compile(r"""'(?:[sdmt]|ll|ve|re)| ?[^\W\d_]+| ?\d{1,3}| ?(?:[^\s\w]|_)+|\s+(?!\S)|\s+""")

EOT = "<|endoftext|>"
SYSTEM = "<|system|>"
USER = "<|user|>"
ASSISTANT = "<|assistant|>"
END = "<|end|>"
SPECIAL_TOKENS = [EOT, SYSTEM, USER, ASSISTANT, END]


def _merge(ids: list[int], pair: tuple[int, int], new_id: int) -> list[int]:
    out, i, n = [], 0, len(ids)
    while i < n:
        if i < n - 1 and ids[i] == pair[0] and ids[i + 1] == pair[1]:
            out.append(new_id)
            i += 2
        else:
            out.append(ids[i])
            i += 1
    return out


class BPETokenizer:
    def __init__(self, merges: list[tuple[int, int]], special_tokens: list[str] | None = None):
        self.merges = [tuple(m) for m in merges]
        self.ranks = {pair: 256 + i for i, pair in enumerate(self.merges)}
        self.vocab = {i: bytes([i]) for i in range(256)}
        for pair, new_id in self.ranks.items():
            self.vocab[new_id] = self.vocab[pair[0]] + self.vocab[pair[1]]
        self.special_tokens = list(SPECIAL_TOKENS if special_tokens is None else special_tokens)
        base = 256 + len(self.merges)
        self.special_to_id = {tok: base + i for i, tok in enumerate(self.special_tokens)}
        self.id_to_special = {i: tok for tok, i in self.special_to_id.items()}
        self._special_re = (
            re.compile("(" + "|".join(re.escape(t) for t in self.special_tokens) + ")")
            if self.special_tokens
            else None
        )
        self._cache: dict[str, list[int]] = {}

    @property
    def vocab_size(self) -> int:
        return 256 + len(self.merges) + len(self.special_tokens)

    def token_id(self, special: str) -> int:
        return self.special_to_id[special]

    # --------------------------------------------------------------- training
    @classmethod
    def train(cls, text: str, vocab_size: int, special_tokens: list[str] | None = None, verbose: bool = False):
        special_tokens = list(SPECIAL_TOKENS if special_tokens is None else special_tokens)
        num_merges = vocab_size - 256 - len(special_tokens)
        if num_merges < 0:
            raise ValueError(f"vocab_size must be at least {256 + len(special_tokens)}")

        counts: Counter = Counter()
        for part in cls._split_special(text, special_tokens):
            if part in special_tokens:
                continue
            counts.update(PATTERN.findall(part))
        words = [list(w.encode("utf-8")) for w in counts]
        freqs = list(counts.values())

        pair_counts: dict[tuple[int, int], int] = defaultdict(int)
        where: dict[tuple[int, int], set[int]] = defaultdict(set)
        for i, w in enumerate(words):
            for pair in zip(w, w[1:]):
                pair_counts[pair] += freqs[i]
                where[pair].add(i)

        merges: list[tuple[int, int]] = []
        for n in range(num_merges):
            if not pair_counts:
                break
            best = max(pair_counts, key=lambda p: (pair_counts[p], -p[0], -p[1]))
            if pair_counts[best] < 2:
                break
            new_id = 256 + n
            merges.append(best)
            for i in list(where[best]):
                w, f = words[i], freqs[i]
                for pair in zip(w, w[1:]):
                    pair_counts[pair] -= f
                    if pair_counts[pair] <= 0:
                        del pair_counts[pair]
                w = _merge(w, best, new_id)
                words[i] = w
                for pair in zip(w, w[1:]):
                    pair_counts[pair] += f
                    where[pair].add(i)
            where.pop(best, None)
            pair_counts.pop(best, None)
            if verbose and (n + 1) % 200 == 0:
                print(f"  bpe merges: {n + 1}/{num_merges}")
        return cls(merges, special_tokens)

    @staticmethod
    def _split_special(text: str, special_tokens: list[str]) -> list[str]:
        if not special_tokens:
            return [text]
        pattern = "(" + "|".join(re.escape(t) for t in special_tokens) + ")"
        return [p for p in re.split(pattern, text) if p]

    # ------------------------------------------------------- encode / decode
    def _encode_chunk(self, chunk: str) -> list[int]:
        cached = self._cache.get(chunk)
        if cached is not None:
            return cached
        ids = list(chunk.encode("utf-8"))
        while len(ids) >= 2:
            pair = min(zip(ids, ids[1:]), key=lambda p: self.ranks.get(p, float("inf")))
            if pair not in self.ranks:
                break
            ids = _merge(ids, pair, self.ranks[pair])
        if len(self._cache) < 100_000:
            self._cache[chunk] = ids
        return ids

    def encode(self, text: str, allow_special: bool = True) -> list[int]:
        out: list[int] = []
        parts = self._special_re.split(text) if (allow_special and self._special_re) else [text]
        for part in parts:
            if not part:
                continue
            if allow_special and part in self.special_to_id:
                out.append(self.special_to_id[part])
                continue
            for chunk in PATTERN.findall(part):
                out.extend(self._encode_chunk(chunk))
        return out

    def decode_bytes(self, ids, skip_special: bool = False) -> bytes:
        parts = []
        for i in ids:
            i = int(i)
            if i in self.vocab:
                parts.append(self.vocab[i])
            elif i in self.id_to_special:
                if not skip_special:
                    parts.append(self.id_to_special[i].encode("utf-8"))
            else:
                raise ValueError(f"unknown token id {i}")
        return b"".join(parts)

    def decode(self, ids, skip_special: bool = False) -> str:
        return self.decode_bytes(ids, skip_special).decode("utf-8", errors="replace")

    # ------------------------------------------------------------ persistence
    def to_dict(self) -> dict:
        return {"type": "byte-bpe", "version": 1, "merges": [list(m) for m in self.merges],
                "special_tokens": self.special_tokens}

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f)

    @classmethod
    def load(cls, path: str) -> "BPETokenizer":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(data["merges"], data["special_tokens"])
