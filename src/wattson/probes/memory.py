"""Memory probe — RAM and swap."""

from __future__ import annotations

import psutil


def _gb(b: int) -> float:
    return b / (1024**3)


def snapshot() -> str:
    v = psutil.virtual_memory()
    s = psutil.swap_memory()
    return (
        f"RAM:    {_gb(v.used):5.1f} / {_gb(v.total):5.1f} GB  ({v.percent:4.1f}%)\n"
        f"Free:   {_gb(v.available):5.1f} GB\n"
        f"Swap:   {_gb(s.used):5.1f} / {_gb(s.total):5.1f} GB  ({s.percent:4.1f}%)"
    )
