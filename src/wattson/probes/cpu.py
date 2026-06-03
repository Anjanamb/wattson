"""CPU probe — usage, frequency, model, temperature.

Temperature is best-effort:
- Linux / FreeBSD: `psutil.sensors_temperatures()` reads /sys/class/hwmon
- macOS: psutil exposes some Apple Silicon sensors
- Windows: psutil has no implementation; would need WMI or OpenHardwareMonitor.
  We report 'n/a' rather than fake a value.
"""

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


def _temp() -> str:
    """Best-effort CPU temperature; returns a human string or 'n/a'."""
    if not hasattr(psutil, "sensors_temperatures"):
        return "n/a"
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        return "n/a"
    if not temps:
        return "n/a"
    # Prefer well-known package sensors when present
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        if key in temps and temps[key]:
            entry = temps[key][0]
            if entry.current is not None:
                return f"{entry.current:.0f}°C"
    # Fall back to the first available chip with a reading
    for chip_entries in temps.values():
        for entry in chip_entries:
            if entry.current is not None:
                return f"{entry.current:.0f}°C"
    return "n/a"


def snapshot() -> str:
    inf = info()
    # interval=0 returns % since the previous call (or 0 on the first call).
    # That's fine for a 1 Hz refresh loop.
    pct = psutil.cpu_percent(interval=0)
    freq = psutil.cpu_freq()
    freq_str = (
        f"{freq.current / 1000:.2f} GHz"
        if freq and freq.current
        else "n/a"
    )
    cores = (
        f"{inf['cores_physical']}P / "
        f"{inf['cores_logical']}L  ({inf['arch']})"
    )
    return (
        f"{inf['brand']}\n"
        f"Cores:  {cores}\n"
        f"Usage:  {pct:5.1f}%\n"
        f"Freq:   {freq_str}\n"
        f"Temp:   {_temp()}"
    )


def metrics() -> dict[str, float]:
    """Scalar metrics for the history buffer.

    interval=0 against psutil keeps this cheap (no second blocking call).
    """
    out = {"cpu.pct": float(psutil.cpu_percent(interval=0))}
    temp = _temp()
    if temp != "n/a" and temp.endswith("°C"):
        try:
            out["cpu.temp"] = float(temp.rstrip("°C"))
        except ValueError:
            pass
    return out
