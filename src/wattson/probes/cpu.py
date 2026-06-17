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


# --- WMI connection + temperature value caches ---
# Recreating a WMI connection is *expensive* (~100-500 ms on Windows).
# CPU temperature also doesn't move fast enough to need a 1 Hz read.
# So: cache the WMI namespace handles for the process lifetime, and
# cache the temperature value for `_TEMP_TTL_SEC` between snapshot calls.
import time as _time

_TEMP_TTL_SEC = 5.0
_wmi_conn_cache: dict[str, object] = {}
_temp_cache = {"value": "n/a", "ts": 0.0}


def _get_wmi(namespace: str):
    """Lazy-init cached WMI client. Returns None if WMI is unavailable
    or the namespace can't be opened."""
    if namespace in _wmi_conn_cache:
        return _wmi_conn_cache[namespace]
    try:
        import wmi  # type: ignore[import-not-found]
    except ImportError:
        _wmi_conn_cache[namespace] = None
        return None
    try:
        _wmi_conn_cache[namespace] = wmi.WMI(namespace=namespace)
    except Exception:
        _wmi_conn_cache[namespace] = None
    return _wmi_conn_cache[namespace]


def _temp_psutil() -> str | None:
    """Linux / FreeBSD / macOS via psutil. Returns None when nothing works."""
    if not hasattr(psutil, "sensors_temperatures"):
        return None
    try:
        temps = psutil.sensors_temperatures()
    except Exception:
        return None
    if not temps:
        return None
    for key in ("coretemp", "k10temp", "zenpower", "cpu_thermal", "acpitz"):
        if key in temps and temps[key]:
            entry = temps[key][0]
            if entry.current is not None:
                return f"{entry.current:.0f}°C"
    for chip_entries in temps.values():
        for entry in chip_entries:
            if entry.current is not None:
                return f"{entry.current:.0f}°C"
    return None


def _temp_wmi() -> str | None:
    """Windows ACPI thermal zone via WMI's `root\\wmi` namespace.

    Uses the cached WMI connection so the per-call cost is just the
    query itself, not the connection setup.
    """
    w = _get_wmi("root\\wmi")
    if w is None:
        return None
    try:
        zones = w.MSAcpi_ThermalZoneTemperature()
    except Exception:
        return None
    if not zones:
        return None
    try:
        kelvin = zones[0].CurrentTemperature / 10.0
        celsius = kelvin - 273.15
        if -50 < celsius < 200:
            return f"{celsius:.0f}°C"
    except Exception:
        pass
    return None


def _temp_lhm() -> str | None:
    """LibreHardwareMonitor / OpenHardwareMonitor WMI provider.

    Cached WMI connection per namespace. Requires LHM/OHM to be running
    (often as admin) with the WMI provider enabled.
    """
    candidates: list[float] = []
    for ns in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
        w = _get_wmi(ns)
        if w is None:
            continue
        try:
            sensors = w.Sensor()
        except Exception:
            continue
        for s in sensors:
            try:
                if getattr(s, "SensorType", "") != "Temperature":
                    continue
                name = getattr(s, "Name", "") or ""
                if "CPU" not in name.upper():
                    continue
                val = getattr(s, "Value", None)
                if val is None:
                    continue
                fval = float(val)
                if -50 < fval < 200:
                    candidates.append(fval)
            except Exception:
                continue
    if not candidates:
        return None
    # Prefer the hottest reading — that's the package, not an idle core.
    return f"{max(candidates):.0f}°C"


def _temp_uncached() -> str:
    """psutil → WMI ACPI → LHM/OHM → 'n/a'. Skips the cache."""
    import sys

    t = _temp_psutil()
    if t is not None:
        return t
    if sys.platform == "win32":
        t = _temp_wmi()
        if t is not None:
            return t
        t = _temp_lhm()
        if t is not None:
            return t
    return "n/a"


def _temp() -> str:
    """Best-effort CPU temperature; cached for `_TEMP_TTL_SEC` so the
    1 Hz TUI refresh isn't blocked by a WMI query every tick. CPU temp
    doesn't move fast enough for sub-second resolution to matter."""
    now = _time.monotonic()
    if now - _temp_cache["ts"] < _TEMP_TTL_SEC:
        return _temp_cache["value"]  # type: ignore[return-value]
    value = _temp_uncached()
    _temp_cache["value"] = value
    _temp_cache["ts"] = now
    return value


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
