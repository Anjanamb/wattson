"""Disk probe — usage per readable partition."""

from __future__ import annotations

import psutil


def _gb(b: int) -> float:
    return b / (1024**3)


def snapshot() -> str:
    rows = []
    for p in psutil.disk_partitions(all=False):
        try:
            u = psutil.disk_usage(p.mountpoint)
        except (PermissionError, OSError):
            continue
        rows.append(
            f"{p.mountpoint:<10}  {_gb(u.used):5.0f} / {_gb(u.total):5.0f} GB  ({u.percent:4.1f}%)"
        )
    return "\n".join(rows) if rows else "no readable partitions"
