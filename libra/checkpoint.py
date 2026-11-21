"""Save and load model checkpoints, published as a unit.

Layout of a checkpoint directory::

    CURRENT                  the name of the checkpoint in use (one line)
    versions/<name>/         config.json, model.npz, tokenizer.json, optimizer.npz (if saved)

``save`` writes a complete new version into a temporary folder, renames it into ``versions/``,
and only then replaces ``CURRENT`` atomically. Readers always see one whole checkpoint, never new
weights with an old tokenizer. An interrupted save leaves ``CURRENT`` on the previous version, so
resuming and the server's reload are unaffected. The last few versions are kept.

Older checkpoints (``config.json`` / ``model.npz`` directly in the directory) still load.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import time

import numpy as np

from .nn import GPT, GPTConfig
from .tokenizer import BPETokenizer

POINTER = "CURRENT"
VERSIONS = "versions"
KEEP = 3  # versions kept, including the current one


def _fsync_file(path: str) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _fsync_dir(path: str) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass  # not every filesystem/platform supports syncing a directory
    finally:
        os.close(fd)


def current_name(directory: str) -> str | None:
    try:
        with open(os.path.join(directory, POINTER), encoding="utf-8") as f:
            name = f.read().strip()
    except OSError:
        return None
    return name or None


def resolve(directory: str) -> str | None:
    """The folder holding the checkpoint in use, or None if there isn't one."""
    name = current_name(directory)
    if name:
        path = os.path.join(directory, VERSIONS, name)
        if os.path.exists(os.path.join(path, "model.npz")):
            return path
    if os.path.exists(os.path.join(directory, "model.npz")):  # the older flat layout
        return directory
    return None


def exists(directory: str) -> bool:
    return resolve(directory) is not None


def version(directory: str) -> str | None:
    """An identity for the checkpoint in use; it changes exactly when a new one is published."""
    name = current_name(directory)
    if name:
        return name
    path = os.path.join(directory, "model.npz")
    try:
        return f"flat:{os.path.getmtime(path)}"
    except OSError:
        return None


def read_meta(directory: str) -> dict:
    path = resolve(directory)
    if path is None:
        return {}
    with open(os.path.join(path, "config.json"), encoding="utf-8") as f:
        return json.load(f).get("meta", {})


def save(directory: str, model: GPT, tokenizer: BPETokenizer, meta: dict | None = None, optimizer=None) -> str:
    """Publish a new checkpoint. Returns its version name."""
    versions = os.path.join(directory, VERSIONS)
    os.makedirs(versions, exist_ok=True)
    name = f"{time.strftime('%Y%m%d-%H%M%S')}-{secrets.token_hex(3)}"
    tmp = os.path.join(versions, name + ".partial")
    os.makedirs(tmp)
    try:
        files = [os.path.join(tmp, "config.json"), os.path.join(tmp, "model.npz"), os.path.join(tmp, "tokenizer.json")]
        with open(files[0], "w", encoding="utf-8") as f:
            json.dump({"model": model.config.to_dict(), "meta": meta or {}}, f, indent=2)
        np.savez(files[1], **model.state_dict())
        tokenizer.save(files[2])
        if optimizer is not None:
            files.append(os.path.join(tmp, "optimizer.npz"))
            np.savez(files[-1], **optimizer.state_dict())
        for path in files:
            _fsync_file(path)
        final = os.path.join(versions, name)
        os.rename(tmp, final)  # the complete version appears at once
        _fsync_dir(versions)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    pointer = os.path.join(directory, POINTER)
    with open(pointer + ".tmp", "w", encoding="utf-8") as f:
        f.write(name + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(pointer + ".tmp", pointer)  # the publish: readers switch to the new version here
    _fsync_dir(directory)
    _prune(directory, name)
    return name


def _prune(directory: str, current: str) -> None:
    versions = os.path.join(directory, VERSIONS)
    names = sorted(n for n in os.listdir(versions) if not n.endswith(".partial") and n != current)
    for old in names[: max(0, len(names) - (KEEP - 1))]:
        shutil.rmtree(os.path.join(versions, old), ignore_errors=True)
    for n in os.listdir(versions):  # leftovers from saves that were interrupted
        path = os.path.join(versions, n)
        if n.endswith(".partial") and time.time() - os.path.getmtime(path) > 3600:
            shutil.rmtree(path, ignore_errors=True)
    for legacy in ("config.json", "model.npz", "tokenizer.json", "optimizer.npz"):  # the old flat layout
        try:
            os.remove(os.path.join(directory, legacy))
        except OSError:
            pass


def load(directory: str):
    """Return ``(model, tokenizer, meta)`` for the checkpoint in use."""
    path = resolve(directory)
    if path is None:
        raise FileNotFoundError(f"no checkpoint in {directory}")
    with open(os.path.join(path, "config.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    model = GPT(GPTConfig(**cfg["model"]))
    with np.load(os.path.join(path, "model.npz")) as data:
        model.load_state_dict({k: data[k] for k in data.files})
    tokenizer = BPETokenizer.load(os.path.join(path, "tokenizer.json"))
    model.eval()
    return model, tokenizer, cfg.get("meta", {})


def load_optimizer_state(directory: str):
    path = resolve(directory)
    if path is None or not os.path.exists(os.path.join(path, "optimizer.npz")):
        return None
    with np.load(os.path.join(path, "optimizer.npz")) as data:
        return {k: data[k] for k in data.files}
