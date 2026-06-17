"""Disk probe — usage per readable partition.

Windows note (v0.0.17): psutil's `disk_usage` C extension can raise
`SystemError` on some Windows configurations (BitLocker-encrypted
volumes mid-unlock, ProtonVPN's virtual drives, removable media in
weird states). We now bypass psutil for the actual usage read on
Windows and call `GetDiskFreeSpaceExW` directly via ctypes — that
gives us the same numbers without the surprise exception class.
psutil is still used for `disk_partitions` since enumerating mounts
works reliably.
"""

from __future__ import annotations

import sys

import psutil


def _gb(b: int) -> float:
    return b / (1024**3)


class _Usage:
    __slots__ = ("total", "used", "free", "percent")


def _windows_disk_usage(mount: str) -> _Usage:
    """Direct GetDiskFreeSpaceExW via ctypes, bypassing psutil.

    Raises OSError on Windows API failure. Returns an object with the
    same `total / used / free / percent` attributes as psutil's
    `sdiskusage` namedtuple so callers don't care which backend ran.
    """
    import ctypes
    from ctypes import wintypes

    free_caller = ctypes.c_ulonglong(0)
    total = ctypes.c_ulonglong(0)
    free_total = ctypes.c_ulonglong(0)

    fn = ctypes.windll.kernel32.GetDiskFreeSpaceExW
    fn.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
        ctypes.POINTER(ctypes.c_ulonglong),
    ]
    fn.restype = wintypes.BOOL

    ok = fn(
        mount,
        ctypes.byref(free_caller),
        ctypes.byref(total),
        ctypes.byref(free_total),
    )
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(err, f"GetDiskFreeSpaceExW failed for {mount}")

    u = _Usage()
    u.total = total.value
    u.free = free_total.value
    u.used = u.total - u.free
    u.percent = (u.used / u.total * 100.0) if u.total else 0.0
    return u


def _disk_usage(mount: str):
    """Cross-platform disk usage: Windows via ctypes, everyone else
    via psutil. Same attribute interface either way."""
    if sys.platform == "win32":
        return _windows_disk_usage(mount)
    return psutil.disk_usage(mount)


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
            u = _disk_usage(p.mountpoint)
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
