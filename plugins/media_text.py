"""Libra plugins: get text out of images, audio and video, so it can go into your knowledge base.

Libra has no vision or speech model (NumPy and the standard library only), so these call an outside tool,
and only when that tool is installed:

  - ``image_ocr``       images → text, via **tesseract** (OCR).           apt install tesseract-ocr
  - ``audio_transcribe``audio → text, via **whisper** (openai-whisper).   pip install -U openai-whisper
  - ``video_transcribe``video → text: **ffmpeg** pulls the audio, then whisper transcribes it.

Each writes the text so you can add it to your files:

    libra skill run image_ocr receipt.jpg --out receipt.txt
    libra kb add receipt.txt

If the tool isn't installed, the skill says so and what to install, instead of failing obscurely. A
scanned page or clear speech works well; noisy audio or artistic images less so. Load with ``--plugins
plugins/`` and accept once: ``libra trust accept-plugin plugins/media_text.py``.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from libra.skills import SkillResult, skill

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp", ".tif", ".tiff", ".ppm", ".pnm"}
AUDIO_EXT = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".aac", ".wma", ".opus"}
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpg", ".mpeg", ".wmv"}


def _need(tool: str, how: str) -> str:
    return f"{tool} isn't installed, so this can't run. Install it ({how}), or put a transcript/description " \
           "in a .txt file next to the media instead."


def _run(cmd: list, timeout: int = 1800) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, timeout=timeout)


def _check(path: str, exts: set) -> str:
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        raise ValueError(f"no such file: {path}")
    if os.path.splitext(path)[1].lower() not in exts:
        raise ValueError(f"{os.path.basename(path)} isn't a supported {'/'.join(sorted(e[1:] for e in exts))[:40]}… file")
    return path


def _save(text: str, out: str, tool: str) -> SkillResult:
    text = text.strip()
    if not text:
        return SkillResult("media_text", False, "No text came out (an empty or unreadable file?).")
    if out:
        out = os.path.expanduser(out)
        with open(out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    shown = text if len(text) <= 4000 else text[:4000].rstrip() + f"\n… ({len(text) - 4000:,} more characters)"
    note = f"\n\n[{len(text):,} characters via {tool}" + (f", written to {out}" if out else "") + "]"
    return SkillResult("media_text", True, shown + note, {"characters": len(text), "tool": tool, "out": out or None,
                                                          "text": text})


# --------------------------------------------------------------------- image
def _route_ocr(message: str):
    import re

    m = re.search(r"\b(?:ocr|read text from|text from)\b.*?([^\s\"']+\.(?:jpe?g|png|gif|webp|bmp|tiff?))\b", message, re.I)
    return {"path": m.group(1)} if m else None


@skill("image_ocr", "Read the text in an image (OCR), so it can be added to your files.",
       {"path": "the image file", "out": "optional .txt to write to", "lang": "tesseract language(s), e.g. eng"},
       router=_route_ocr, risk="read")
def image_ocr(path: str, out: str = "", lang: str = "eng") -> SkillResult:
    path = _check(path, IMAGE_EXT)
    if not shutil.which("tesseract"):
        return SkillResult("image_ocr", False, _need("tesseract (OCR)", "apt install tesseract-ocr"))
    r = _run(["tesseract", path, "stdout", "-l", lang], timeout=300)
    if r.returncode != 0:
        return SkillResult("image_ocr", False, f"tesseract failed: {r.stderr.decode('utf-8', 'replace')[:300]}")
    return _save(r.stdout.decode("utf-8", "replace"), out, "tesseract")


# --------------------------------------------------------------------- audio
def _whisper(path: str, model: str, language: str) -> tuple[str | None, str]:
    """Transcribe with openai-whisper. Returns (text or None, error)."""
    if not shutil.which("whisper"):
        return None, _need("whisper", "pip install -U openai-whisper")
    with tempfile.TemporaryDirectory() as d:
        cmd = ["whisper", path, "--model", model, "--output_format", "txt", "--output_dir", d, "--fp16", "False"]
        if language:
            cmd += ["--language", language]
        r = _run(cmd)
        if r.returncode != 0:
            return None, f"whisper failed: {r.stderr.decode('utf-8', 'replace')[:300]}"
        stem = os.path.splitext(os.path.basename(path))[0]
        txt = os.path.join(d, stem + ".txt")
        if not os.path.isfile(txt):
            return None, "whisper produced no transcript file"
        with open(txt, encoding="utf-8") as f:
            return f.read(), ""


@skill("audio_transcribe", "Transcribe an audio file to text, so it can be added to your files.",
       {"path": "the audio file", "out": "optional .txt to write to", "model": "whisper model, e.g. base, small",
        "language": "optional language code, e.g. en"}, risk="read")
def audio_transcribe(path: str, out: str = "", model: str = "base", language: str = "") -> SkillResult:
    path = _check(path, AUDIO_EXT)
    text, err = _whisper(path, model, language)
    if text is None:
        return SkillResult("audio_transcribe", False, err)
    return _save(text, out, f"whisper ({model})")


# --------------------------------------------------------------------- video
@skill("video_transcribe", "Transcribe a video's speech to text (ffmpeg pulls the audio, whisper transcribes).",
       {"path": "the video file", "out": "optional .txt to write to", "model": "whisper model, e.g. base"},
       risk="read")
def video_transcribe(path: str, out: str = "", model: str = "base") -> SkillResult:
    path = _check(path, VIDEO_EXT)
    if not shutil.which("ffmpeg"):
        return SkillResult("video_transcribe", False, _need("ffmpeg", "apt install ffmpeg"))
    if not shutil.which("whisper"):
        return SkillResult("video_transcribe", False, _need("whisper", "pip install -U openai-whisper"))
    with tempfile.TemporaryDirectory() as d:
        wav = os.path.join(d, "audio.wav")
        r = _run(["ffmpeg", "-y", "-i", path, "-ar", "16000", "-ac", "1", "-vn", wav], timeout=600)
        if r.returncode != 0 or not os.path.isfile(wav):
            return SkillResult("video_transcribe", False,
                               f"ffmpeg couldn't extract audio: {r.stderr.decode('utf-8', 'replace')[:300]}")
        text, err = _whisper(wav, model, "")
    if text is None:
        return SkillResult("video_transcribe", False, err)
    return _save(text, out, f"ffmpeg + whisper ({model})")
