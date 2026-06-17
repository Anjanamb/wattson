"""Disk probe — usage per readable partition."""

from __future__ import annotations

import psutil


def _gb(b: int) -> float:
    return b / (1024**3)


# Disk usage doesn't move every second, and on the user's Windows box
# `psutil.disk_usage('C:\\')` was raising `SystemError` — re-raising that
# at 1 Hz visibly slowed the TUI. Cache the rendered string for
# `_DISK_TTL_SEC` so we pay the cost (success or failure) at most once
# every 10 s.
import time as _time

_DISK_TTL_SEC = 10.0
_disk_cache: dict = {"value": None, "ts": 0.0}


def snapshot() -> str:
    now = _time.monotonic()
    cached = _disk_cache["value"]
    if cached is not None and now - _disk_cache["ts"] < _DISK_TTL_SEC:
        return cached
    value = _snapshot_uncached()
    _disk_cache["value"] = value
    _disk_cache["ts"] = now
    return value


def _snapshot_uncached() -> str:
    """Render one line per readable partition.

    Tries `all=False` first (mounted/fixed only), falls back to
    `all=True` if the strict call returns nothing — some Windows boxes
    expose only removable volumes in the strict list. When partitions
    *do* exist but every `disk_usage` call fails, the panel surfaces
    the first exception class so we don't silently lie with
    'no readable partitions'.
    """
    parts = _list_partitions()
    if not parts:
        return "no partitions returned by psutil"
    rows: list[str] = []
    first_err: str | None = None
    for p in parts:
        try:
            u = psutil.disk_usage(p.mountpoint)
            mount = p.mountpoint or "?"
            rows.append(
                f"{mount:<10}  {_gb(u.used):5.0f} / {_gb(u.total):5.0f} GB"
                f"  ({u.percent:4.1f}%)"
            )
        except Exception as e:
            # psutil's Windows C extension can throw OSError, struct.error,
            # ValueError, etc. on locked / offline / network volumes. Keep
            # going so one bad mount doesn't blank the panel, but remember
            # the first failure so the user gets a diagnostic message if
            # *everything* fails.
            if first_err is None:
                first_err = f"{p.mountpoint}: {type(e).__name__}"
            continue
    if rows:
        return "\n".join(rows)
    if first_err is not None:
        return f"all partitions failed — e.g. {first_err}"
    return "no readable partitions"


def _list_partitions():
    """Try the strict list first, then the relaxed one if it's empty."""
    try:
        parts = psutil.disk_partitions(all=False)
        if parts:
            return parts
    except Exception:
        pass
    try:
        return psutil.disk_partitions(all=True)
    except Exception:
        return []
