"""CPU probe — usage, frequency, model."""

from __future__ import annotations

from functools import lru_cache

import psutil


@lru_cache(maxsize=1)
def info() -> dict:
    """Static CPU info — cached; doesn't change at runtime."""
    brand = "Unknown CPU"
    arch = "?"
    try:
        import cpuinfo

        ci = cpuinfo.get_cpu_info()
        brand = ci.get("brand_raw") or brand
        arch = ci.get("arch") or arch
    except Exception:
        pass

    return {
        "brand": brand,
        "arch": arch,
        "cores_physical": psutil.cpu_count(logical=False) or 0,
        "cores_logical": psutil.cpu_count(logical=True) or 0,
    }


def snapshot() -> str:
    inf = info()
    # interval=0 returns the % since the previous call (or 0 on the first call).
    # That's fine for a 1 Hz refresh loop.
    pct = psutil.cpu_percent(interval=0)
    freq = psutil.cpu_freq()
    freq_str = f"{freq.current / 1000:.2f} GHz" if freq and freq.current else "n/a"
    return (
        f"{inf['brand']}\n"
        f"Cores:  {inf['cores_physical']}P / {inf['cores_logical']}L  ({inf['arch']})\n"
        f"Usage:  {pct:5.1f}%\n"
        f"Freq:   {freq_str}"
    )
