"""NVIDIA GPU probe via NVML.

Reports util, VRAM, temp, clocks, power, and any active throttle reasons.
Gracefully degrades when no NVIDIA driver/GPU is present so the TUI panel
never crashes. Each NVML call is individually fenced so an Optimus laptop
or older driver that doesn't expose (say) `nvmlDeviceGetPowerUsage` still
shows the rest of the fields.

Future: AMD (ROCm / rocm-smi) and Apple Silicon (powermetrics) backends
behind the same `snapshot()` contract.
"""

from __future__ import annotations

_state = {"initialized": False, "available": False}


def _init() -> bool:
    if _state["initialized"]:
        return _state["available"]
    _state["initialized"] = True
    try:
        from pynvml import nvmlInit

        nvmlInit()
        _state["available"] = True
    except Exception:
        _state["available"] = False
    return _state["available"]


def _gb(b: int) -> float:
    return b / (1024**3)


# Throttle-reason bits -> short human labels. `GpuIdle` (0x1) is intentionally
# omitted — "throttled because nothing's running" is noise, not a finding.
_THROTTLE_LABELS = [
    (0x4,   "PowerCap (sw)"),
    (0x20,  "Thermal (sw)"),
    (0x40,  "Thermal (hw)"),
    (0x8,   "HW slowdown"),
    (0x80,  "Power brake"),
    (0x2,   "App clocks"),
    (0x10,  "SyncBoost"),
    (0x100, "Display clk"),
]


def _throttle_text(mask: int) -> str:
    hits = [label for bit, label in _THROTTLE_LABELS if mask & bit]
    return ", ".join(hits)


def snapshot() -> str:
    if not _init():
        return (
            "No NVIDIA GPU / driver detected.\n"
            "(install driver and nvidia-ml-py)"
        )
    try:
        from pynvml import (
            NVML_CLOCK_GRAPHICS,
            NVML_CLOCK_MEM,
            NVML_TEMPERATURE_GPU,
            nvmlDeviceGetClockInfo,
            nvmlDeviceGetCount,
            nvmlDeviceGetCurrentClocksThrottleReasons,
            nvmlDeviceGetHandleByIndex,
            nvmlDeviceGetMemoryInfo,
            nvmlDeviceGetName,
            nvmlDeviceGetPowerManagementLimit,
            nvmlDeviceGetPowerUsage,
            nvmlDeviceGetTemperature,
            nvmlDeviceGetUtilizationRates,
        )

        n = nvmlDeviceGetCount()
        if n == 0:
            return "No NVIDIA GPUs found."

        out = []
        for i in range(n):
            h = nvmlDeviceGetHandleByIndex(i)
            name = nvmlDeviceGetName(h)
            if isinstance(name, bytes):
                name = name.decode()
            util = nvmlDeviceGetUtilizationRates(h)
            mem = nvmlDeviceGetMemoryInfo(h)
            temp = nvmlDeviceGetTemperature(h, NVML_TEMPERATURE_GPU)

            # Per-call fallbacks — some Optimus / older drivers omit fields.
            def _try(fn, *args):
                try:
                    return fn(*args)
                except Exception:
                    return None

            gclk = _try(nvmlDeviceGetClockInfo, h, NVML_CLOCK_GRAPHICS)
            mclk = _try(nvmlDeviceGetClockInfo, h, NVML_CLOCK_MEM)
            pwr_mw = _try(nvmlDeviceGetPowerUsage, h)
            pcap_mw = _try(nvmlDeviceGetPowerManagementLimit, h)
            throttle_mask = (
                _try(nvmlDeviceGetCurrentClocksThrottleReasons, h) or 0
            )

            clk = (f"{gclk}/{mclk} MHz" if gclk and mclk
                   else f"{gclk} MHz" if gclk
                   else "n/a")
            if pwr_mw is not None and pcap_mw is not None:
                pwr = f"{pwr_mw / 1000:.0f} / {pcap_mw / 1000:.0f} W"
            elif pwr_mw is not None:
                pwr = f"{pwr_mw / 1000:.0f} W"
            else:
                pwr = "n/a"

            head = (
                f"  Util:  {util.gpu:3d}% · "
                f"MemBW: {util.memory:3d}% · {temp}°C"
            )
            block = (
                f"GPU{i}: {name}\n"
                f"{head}\n"
                f"  VRAM:  {_gb(mem.used):5.1f} / {_gb(mem.total):5.1f} GB\n"
                f"  Clock: {clk}  ·  Power: {pwr}"
            )
            throttle = _throttle_text(throttle_mask)
            if throttle:
                block += f"\n  [yellow]Throttle:[/yellow] {throttle}"
            out.append(block)
        return "\n\n".join(out)
    except Exception as e:
        return f"NVML error: {e}"


def metrics() -> dict[str, float]:
    """Per-GPU scalar metrics for the history buffer.

    Keys are `gpu{i}.{util|mem_bw|temp|power}`. Each NVML call is
    individually fenced so a partial driver still surfaces what it can.
    """
    if not _init():
        return {}
    out: dict[str, float] = {}
    try:
        from pynvml import (
            NVML_TEMPERATURE_GPU,
            nvmlDeviceGetCount,
            nvmlDeviceGetHandleByIndex,
            nvmlDeviceGetPowerUsage,
            nvmlDeviceGetTemperature,
            nvmlDeviceGetUtilizationRates,
        )

        n = nvmlDeviceGetCount()
        for i in range(n):
            try:
                h = nvmlDeviceGetHandleByIndex(i)
            except Exception:
                continue
            try:
                util = nvmlDeviceGetUtilizationRates(h)
                out[f"gpu{i}.util"] = float(util.gpu)
                out[f"gpu{i}.mem_bw"] = float(util.memory)
            except Exception:
                pass
            try:
                out[f"gpu{i}.temp"] = float(
                    nvmlDeviceGetTemperature(h, NVML_TEMPERATURE_GPU)
                )
            except Exception:
                pass
            try:
                out[f"gpu{i}.power"] = nvmlDeviceGetPowerUsage(h) / 1000.0
            except Exception:
                pass
    except Exception:
        return out
    return out


def device_count() -> int:
    """Return the number of NVIDIA devices (0 if no driver / no GPUs)."""
    if not _init():
        return 0
    try:
        from pynvml import nvmlDeviceGetCount

        return int(nvmlDeviceGetCount())
    except Exception:
        return 0
