"""Array backend: NumPy on the CPU, or CuPy on an NVIDIA GPU.

All tensor math goes through ``backend.xp``. Call :func:`use` before building
or loading a model to choose the device.
"""
from __future__ import annotations

import numpy as np

xp = np  # the active array module
_cupy = None


def _import_cupy():
    global _cupy
    if _cupy is None:
        import cupy  # noqa: F401  (optional dependency)

        cupy.cuda.runtime.getDeviceCount()  # raises if no usable GPU / driver
        _cupy = cupy
    return _cupy


def gpu_available() -> bool:
    try:
        _import_cupy()
        return True
    except Exception:
        return False


def use(device: str = "auto") -> str:
    """Select ``"cpu"``, ``"gpu"`` or ``"auto"`` (GPU if CuPy and a GPU are present). Returns the device used."""
    global xp
    device = (device or "auto").lower()
    if device in ("gpu", "cuda"):
        try:
            xp = _import_cupy()
        except Exception as exc:
            raise RuntimeError(
                "GPU requested but CuPy or a CUDA GPU is not available. "
                "Install the CuPy build for your CUDA version, e.g. pip install cupy-cuda12x"
            ) from exc
    elif device == "auto":
        xp = _cupy if gpu_available() else np
    elif device == "cpu":
        xp = np
    else:
        raise ValueError(f"unknown device {device!r}; use cpu, gpu or auto")
    return name()


def name() -> str:
    return "cpu" if xp is np else "gpu"


def on_gpu() -> bool:
    return xp is not np


def asarray(x, dtype=None):
    """Move ``x`` to the active device."""
    if isinstance(x, xp.ndarray):
        return x if dtype is None else x.astype(dtype, copy=False)
    if xp is not np and isinstance(x, np.ndarray):
        return xp.asarray(x, dtype=dtype)
    return xp.asarray(np.asarray(x) if dtype is None else np.asarray(x, dtype=dtype))


def to_numpy(x) -> np.ndarray:
    if isinstance(x, np.ndarray):
        return x
    if _cupy is not None and isinstance(x, _cupy.ndarray):
        return _cupy.asnumpy(x)
    return np.asarray(x)


def scatter_add(target, indices, values) -> None:
    """``target[indices] += values`` with repeated indices accumulated."""
    if xp is np:
        np.add.at(target, indices, values)
    else:
        import cupyx

        cupyx.scatter_add(target, indices, values)
