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

    Many modern laptops don't expose CPU temperature here — the value
    lives in EC registers behind vendor drivers. `_temp_lhm()` handles
    those when LibreHardwareMonitor (or OpenHardwareMonitor) is
    running with its WMI provider enabled.
    """
    try:
        import wmi  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        w = wmi.WMI(namespace="root\\wmi")
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

    LHM exposes its sensors in `root\\LibreHardwareMonitor`; older OHM
    used `root\\OpenHardwareMonitor`. Each Sensor row carries `Name`,
    `SensorType`, `Value`. We look for a Temperature sensor whose Name
    mentions CPU and return the first non-empty Value.

    Requires LHM/OHM to be running (often as admin) with the WMI
    provider enabled. Returns None otherwise — quiet best-effort.
    """
    try:
        import wmi  # type: ignore[import-not-found]
    except ImportError:
        return None

    candidates: list[float] = []
    for ns in ("root\\LibreHardwareMonitor", "root\\OpenHardwareMonitor"):
        try:
            w = wmi.WMI(namespace=ns)
        except Exception:
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


def _temp() -> str:
    """Best-effort CPU temperature; returns a human string or 'n/a'.

    Order:
      1. psutil sensors (Linux / FreeBSD / macOS)
      2. Windows WMI ACPI thermal zone (some Intel boxes)
      3. LibreHardwareMonitor / OpenHardwareMonitor WMI provider
         (modern laptops where ACPI hides the EC temperature)

    Falls back to 'n/a' when none of the above work.
    """
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
