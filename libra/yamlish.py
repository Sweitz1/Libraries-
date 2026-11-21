"""A small YAML reader covering what API specs use (no external library).

Supported: block mappings and sequences, ``- key: value`` items, plain,
single- and double-quoted scalars, multi-line plain scalars, ``|`` and ``>``
block scalars (with ``-``/``+`` chomping), flow ``[...]`` and ``{...}``,
comments, ``---`` document markers, numbers, booleans and null.
Not supported: anchors/aliases, tags, multiple documents, complex keys.
"""
from __future__ import annotations

import json
import re


class YAMLError(ValueError):
    pass


_INT = re.compile(r"[-+]?(0|[1-9][0-9_]*)$")
_FLOAT = re.compile(r"[-+]?(\d[\d_]*)?\.\d+([eE][-+]?\d+)?$|[-+]?\d[\d_]*[eE][-+]?\d+$")


def _scalar(text: str):
    t = text.strip()
    if t in ("", "~", "null", "Null", "NULL"):
        return None
    if t in ("true", "True", "TRUE"):
        return True
    if t in ("false", "False", "FALSE"):
        return False
    if _INT.match(t):
        return int(t.replace("_", ""))
    if _FLOAT.match(t):
        return float(t.replace("_", ""))
    if t[0] in "&*!" and not t.startswith("!="):
        raise YAMLError(f"anchors, aliases and tags are not supported: {t[:30]!r}")
    return t


def _unquote(t: str, line: int):
    if t[0] == '"':
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            # YAML allows a few escapes JSON doesn't; fall back to a literal read
            return t[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return t[1:-1].replace("''", "'")


def _strip_comment(text: str) -> str:
    quote = None
    depth = 0
    for i, ch in enumerate(text):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'" and (i == 0 or text[i - 1] in " [{,:"):
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "#" and (i == 0 or text[i - 1] in " \t"):
            return text[:i].rstrip()
    return text.rstrip()


def _split_key(text: str):
    """``key: rest`` -> (key, rest); None if the line isn't a mapping entry."""
    if text[0] in "\"'":
        end = text.find(text[0], 1)
        while end > 0 and text[0] == "'" and text[end + 1 : end + 2] == "'":
            end = text.find("'", end + 2)
        if end > 0 and text[end + 1 : end + 2] == ":" and (len(text) == end + 2 or text[end + 2] in " \t"):
            return _unquote(text[: end + 1], 0), text[end + 2 :].strip()
        return None
    if text[0] in "[{":
        return None
    m = re.match(r"([^:#]|:(?=\S))+?:(\s+|$)", text)
    if not m:
        return None
    key = text[: m.end()].rstrip()[:-1].strip()
    return key, text[m.end():].strip()


class _Flow:
    def __init__(self, text: str):
        self.s, self.i = text, 0

    def parse(self):
        v = self.value()
        self.ws()
        if self.i != len(self.s):
            raise YAMLError(f"unexpected text after flow value: {self.s[self.i:]!r}")
        return v

    def ws(self):
        while self.i < len(self.s) and self.s[self.i] in " \t\n":
            self.i += 1

    def value(self):
        self.ws()
        c = self.s[self.i : self.i + 1]
        if c == "[":
            self.i += 1
            out = []
            while True:
                self.ws()
                if self.s[self.i : self.i + 1] == "]":
                    self.i += 1
                    return out
                out.append(self.value())
                self.ws()
                if self.s[self.i : self.i + 1] == ",":
                    self.i += 1
        if c == "{":
            self.i += 1
            out = {}
            while True:
                self.ws()
                if self.s[self.i : self.i + 1] == "}":
                    self.i += 1
                    return out
                key = self.value()
                self.ws()
                val = None
                if self.s[self.i : self.i + 1] == ":":
                    self.i += 1
                    val = self.value()
                out[key] = val
                self.ws()
                if self.s[self.i : self.i + 1] == ",":
                    self.i += 1
        if c in ("'", '"'):
            j = self.i + 1
            while j < len(self.s):
                if self.s[j] == "\\" and c == '"':
                    j += 2
                    continue
                if self.s[j] == c:
                    if c == "'" and self.s[j + 1 : j + 2] == "'":
                        j += 2
                        continue
                    break
                j += 1
            raw = self.s[self.i : j + 1]
            self.i = j + 1
            return _unquote(raw, 0)
        m = re.compile(r"[^,\]\}]*?(?=\s*(,|\]|\}|: |:$|$))").match(self.s, self.i)
        if m is None or self.i >= len(self.s):
            raise YAMLError(f"bad flow value near {self.s[self.i:self.i + 20]!r}")
        self.i = m.end()
        return _scalar(m.group())


def _value(text: str, line: int):
    if text[0] in "[{":
        return _Flow(text).parse()
    if text[0] in "\"'":
        if text[-1] != text[0] or len(text) < 2:
            raise YAMLError(f"line {line}: unterminated quoted string")
        return _unquote(text, line)
    return _scalar(text)


class _Parser:
    def __init__(self, text: str):
        self.raw = [l.rstrip("\r") for l in text.replace("\t", "    ").split("\n")]
        self.i = 0

    def peek(self):
        """(line index, indent, content) of the next meaningful line, or None."""
        j = self.i
        while j < len(self.raw):
            line = self.raw[j]
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and stripped not in ("---", "..."):
                content = _strip_comment(line.lstrip())
                if content:
                    return j, len(line) - len(line.lstrip()), content
            j += 1
        return None

    @staticmethod
    def is_item(content: str) -> bool:
        return content == "-" or content.startswith("- ")

    def parse(self):
        nxt = self.peek()
        if nxt is None:
            return None
        value = self.block(nxt[1])
        if self.peek() is not None:
            j, _, content = self.peek()
            raise YAMLError(f"line {j + 1}: unexpected indentation near {content[:40]!r}")
        return value

    def block(self, indent: int):
        j, ind, content = self.peek()
        if self.is_item(content):
            return self.seq(ind)
        if _split_key(content) is not None:
            return self.mapping(ind)
        self.i = j + 1
        return self.continue_scalar(_value(content, j + 1), content, indent - 1)

    def continue_scalar(self, value, content, indent):
        """Join following more-indented lines into a multi-line plain scalar."""
        if not isinstance(value, str) or content[0] in "\"'[{":
            return value
        parts = [value]
        while True:
            nxt = self.peek()
            if nxt is None or nxt[1] <= indent or (_split_key(nxt[2]) and nxt[1] <= indent + 2) or self.is_item(nxt[2]):
                break
            parts.append(nxt[2])
            self.i = nxt[0] + 1
        return " ".join(parts)

    def seq(self, ind: int):
        items = []
        while True:
            nxt = self.peek()
            if nxt is None or nxt[1] != ind or not self.is_item(nxt[2]):
                return items
            j, _, content = nxt
            rest = content[1:].strip()
            if not rest:
                self.i = j + 1
                child = self.peek()
                items.append(self.block(child[1]) if child and child[1] > ind else None)
            elif _split_key(rest) is not None and rest[0] not in "[{":
                col = ind + len(content) - len(rest)
                self.raw[j] = " " * col + rest  # re-read the rest as a mapping at its own column
                items.append(self.mapping(col))
            elif rest[0] in "|>":
                self.i = j + 1
                items.append(self.block_scalar(rest, ind))
            else:
                self.i = j + 1
                items.append(self.continue_scalar(_value(rest, j + 1), rest, ind))

    def mapping(self, ind: int):
        out = {}
        while True:
            nxt = self.peek()
            if nxt is None or nxt[1] != ind or self.is_item(nxt[2]):
                return out
            j, _, content = nxt
            kv = _split_key(content)
            if kv is None:
                raise YAMLError(f"line {j + 1}: expected 'key: value', got {content[:40]!r}")
            key, rest = kv
            key = _scalar(key) if isinstance(key, str) and not content.startswith(("'", '"')) else key
            if isinstance(key, (int, float, bool)) or key is None:
                key = str(key).lower() if isinstance(key, bool) else ("null" if key is None else str(key))
            self.i = j + 1
            if not rest:
                child = self.peek()
                if child and child[1] > ind:
                    out[key] = self.block(child[1])
                elif child and child[1] == ind and self.is_item(child[2]):
                    out[key] = self.seq(ind)
                else:
                    out[key] = None
            elif rest[0] in "|>":
                out[key] = self.block_scalar(rest, ind)
            else:
                out[key] = self.continue_scalar(_value(rest, j + 1), rest, ind)

    def block_scalar(self, header: str, ind: int) -> str:
        style, chomp = header[0], "clip"
        if "-" in header:
            chomp = "strip"
        elif "+" in header:
            chomp = "keep"
        lines, block_indent = [], None
        while self.i < len(self.raw):
            line = self.raw[self.i]
            if line.strip():
                cur = len(line) - len(line.lstrip())
                if cur <= ind:
                    break
                if block_indent is None:
                    block_indent = cur
                if cur < block_indent:
                    break
                lines.append(line[block_indent:])
            else:
                lines.append("")
            self.i += 1
        while lines and not lines[-1] and chomp != "keep":
            lines.pop()
        if style == "|":
            text = "\n".join(lines)
        else:
            text, para = "", []
            for line in lines:
                if line and not line.startswith(" "):
                    para.append(line)
                else:
                    text += " ".join(para) + "\n" + (line + "\n" if line else "")
                    para = []
            text = (text + " ".join(para)).strip("\n") if para else text.rstrip("\n")
        return text if chomp == "strip" else text + "\n"


def loads(text: str):
    return _Parser(text).parse()
