"""Memory probe — RAM and swap."""

from __future__ import annotations

import psutil


def _gb(b: int) -> float:
    return b / (1024**3)


def snapshot() -> str:
    v = psutil.virtual_memory()
    s = psutil.swap_memory()
    ram = f"{_gb(v.used):5.1f} / {_gb(v.total):5.1f} GB  ({v.percent:4.1f}%)"
    swap = f"{_gb(s.used):5.1f} / {_gb(s.total):5.1f} GB  ({s.percent:4.1f}%)"
    return (
        f"RAM:    {ram}\n"
        f"Free:   {_gb(v.available):5.1f} GB\n"
        f"Swap:   {swap}"
    )


def metrics() -> dict[str, float]:
    """Scalar metrics for the history buffer."""
    v = psutil.virtual_memory()
    s = psutil.swap_memory()
    return {
        "mem.pct": float(v.percent),
        "swap.pct": float(s.percent),
    }
