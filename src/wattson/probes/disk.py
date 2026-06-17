"""Disk probe — usage per readable partition."""

from __future__ import annotations

import psutil


def _gb(b: int) -> float:
    return b / (1024**3)


def snapshot() -> str:
    rows = []
    try:
        parts = psutil.disk_partitions(all=False)
    except Exception:
        return "no readable partitions"
    for p in parts:
        # psutil can raise OSError, PermissionError, struct.error,
        # ValueError and others on locked / offline / removable / weird
        # Windows volumes. Any failure for one partition should not abort
        # the whole snapshot.
        try:
            u = psutil.disk_usage(p.mountpoint)
            mount = p.mountpoint or "?"
            used_gb = _gb(u.used)
            total_gb = _gb(u.total)
            pct = u.percent
            rows.append(
                f"{mount:<10}  {used_gb:5.0f} / {total_gb:5.0f} GB"
                f"  ({pct:4.1f}%)"
            )
        except Exception:
            continue
    return "\n".join(rows) if rows else "no readable partitions"
