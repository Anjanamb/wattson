"""NVIDIA GPU probe via NVML.

Reports util, VRAM, temp, clocks, power, and any active throttle reasons.
Gracefully degrades when no NVIDIA driver/GPU is present so the TUI panel
never crashes. Each NVML call is individually fenced so an Optimus laptop
or older driver that doesn't expose (say) `nvmlDeviceGetPowerUsage` still
shows the rest of the fields.

Future: AMD (ROCm / rocm-smi) and Apple Silicon (powermetrics) backends
behind the same `snapshot()` contract.

---- Caching note ----
NVML calls are remarkably slow on some Windows driver versions (~5-30 ms
each, and there are ~14 of them per second across snapshot() + metrics()
+ throttle_masks()). On a laptop that visibly stalls the TUI.

To fix: every call goes through `_collect()`, which runs the full set of
NVML reads once and caches the result for `_TTL_SEC = 0.5`. The three
public functions then format that cached dict in different ways. Half a
second is well within the live-feel window for a system monitor.
"""

from __future__ import annotations

import time as _time

_state = {"initialized": False, "available": False}

_TTL_SEC = 0.5
_cache: dict = {"data": None, "ts": 0.0}


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


def _try(fn, *args):
    """Per-call fallback for partial-info drivers."""
    try:
        return fn(*args)
    except Exception:
        return None


def _collect_uncached() -> dict | None:
    """One NVML pass collecting everything snapshot/metrics/throttle need.

    Returns `None` when NVML is unavailable, an empty dict when zero
    GPUs are present, or `{i: {name, util, mem_bw, vram_used, vram_total,
    temp, gclk, mclk, power_w, power_cap_w, throttle_mask}}` indexed by
    GPU number. Each NVML call individually fenced so a partial driver
    still surfaces what it can.
    """
    if not _init():
        return None
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
    except Exception:
        return None

    try:
        n = nvmlDeviceGetCount()
    except Exception:
        return None

    devices: dict[int, dict] = {}
    for i in range(n):
        try:
            h = nvmlDeviceGetHandleByIndex(i)
        except Exception:
            continue

        name = _try(nvmlDeviceGetName, h) or "?"
        if isinstance(name, bytes):
            name = name.decode(errors="replace")

        util = _try(nvmlDeviceGetUtilizationRates, h)
        mem = _try(nvmlDeviceGetMemoryInfo, h)
        temp = _try(nvmlDeviceGetTemperature, h, NVML_TEMPERATURE_GPU)
        gclk = _try(nvmlDeviceGetClockInfo, h, NVML_CLOCK_GRAPHICS)
        mclk = _try(nvmlDeviceGetClockInfo, h, NVML_CLOCK_MEM)
        pwr_mw = _try(nvmlDeviceGetPowerUsage, h)
        pcap_mw = _try(nvmlDeviceGetPowerManagementLimit, h)
        mask = _try(nvmlDeviceGetCurrentClocksThrottleReasons, h) or 0

        devices[i] = {
            "name": str(name),
            "util": float(util.gpu) if util is not None else None,
            "mem_bw": float(util.memory) if util is not None else None,
            "vram_used": int(mem.used) if mem is not None else None,
            "vram_total": int(mem.total) if mem is not None else None,
            "temp": float(temp) if temp is not None else None,
            "gclk": int(gclk) if gclk is not None else None,
            "mclk": int(mclk) if mclk is not None else None,
            "power_w": (pwr_mw / 1000.0) if pwr_mw is not None else None,
            "power_cap_w": (pcap_mw / 1000.0) if pcap_mw is not None else None,
            "throttle_mask": int(mask),
        }
    return devices


def _collect() -> dict | None:
    """Cached `_collect_uncached`. TTL = `_TTL_SEC` (0.5 s).

    snapshot(), metrics(), and throttle_masks() all share this — that's
    the whole point of the cache. Without it the three functions would
    each trigger their own NVML round, ~14 calls/sec total, and on
    Windows that visibly stalls the TUI.
    """
    now = _time.monotonic()
    if _cache["data"] is not None and now - _cache["ts"] < _TTL_SEC:
        return _cache["data"]
    data = _collect_uncached()
    _cache["data"] = data
    _cache["ts"] = now
    return data


def snapshot() -> str:
    data = _collect()
    if data is None:
        return (
            "No NVIDIA GPU / driver detected.\n"
            "(install driver and nvidia-ml-py)"
        )
    if not data:
        return "No NVIDIA GPUs found."

    out = []
    for i, d in data.items():
        util = f"{int(d['util']):3d}" if d["util"] is not None else "  ?"
        membw = f"{int(d['mem_bw']):3d}" if d["mem_bw"] is not None else "  ?"
        temp = f"{int(d['temp'])}" if d["temp"] is not None else "?"
        head = f"  Util:  {util}% · MemBW: {membw}% · {temp}°C"

        if d["vram_used"] is not None and d["vram_total"] is not None:
            vram = (
                f"  VRAM:  {_gb(d['vram_used']):5.1f} / "
                f"{_gb(d['vram_total']):5.1f} GB"
            )
        else:
            vram = "  VRAM:  n/a"

        if d["gclk"] and d["mclk"]:
            clk = f"{d['gclk']}/{d['mclk']} MHz"
        elif d["gclk"]:
            clk = f"{d['gclk']} MHz"
        else:
            clk = "n/a"

        if d["power_w"] is not None and d["power_cap_w"] is not None:
            pwr = f"{d['power_w']:.0f} / {d['power_cap_w']:.0f} W"
        elif d["power_w"] is not None:
            pwr = f"{d['power_w']:.0f} W"
        else:
            pwr = "n/a"

        block = (
            f"GPU{i}: {d['name']}\n"
            f"{head}\n"
            f"{vram}\n"
            f"  Clock: {clk}  ·  Power: {pwr}"
        )
        throttle = _throttle_text(d["throttle_mask"])
        if throttle:
            block += f"\n  [yellow]Throttle:[/yellow] {throttle}"
        out.append(block)
    return "\n\n".join(out)


def metrics() -> dict[str, float]:
    """Per-GPU scalar metrics for the history buffer.
    Keys: `gpu{i}.{util|mem_bw|temp|power|vram_pct}`.
    """
    data = _collect()
    if not data:
        return {}
    out: dict[str, float] = {}
    for i, d in data.items():
        if d["util"] is not None:
            out[f"gpu{i}.util"] = d["util"]
        if d["mem_bw"] is not None:
            out[f"gpu{i}.mem_bw"] = d["mem_bw"]
        if d["temp"] is not None:
            out[f"gpu{i}.temp"] = d["temp"]
        if d["power_w"] is not None:
            out[f"gpu{i}.power"] = d["power_w"]
        if (d["vram_used"] is not None
                and d["vram_total"] is not None
                and d["vram_total"] > 0):
            out[f"gpu{i}.vram_pct"] = 100.0 * d["vram_used"] / d["vram_total"]
    return out


def throttle_masks() -> dict[int, int]:
    """Per-GPU current throttle-reason bitmask. Empty if no NVML."""
    data = _collect()
    if not data:
        return {}
    return {i: d["throttle_mask"] for i, d in data.items()}


def throttle_text(mask: int) -> str:
    """Public alias for the internal label resolver — used by watchdog."""
    return _throttle_text(mask)


def device_count() -> int:
    """Return the number of NVIDIA devices (0 if no driver / no GPUs)."""
    if not _init():
        return 0
    try:
        from pynvml import nvmlDeviceGetCount

        return int(nvmlDeviceGetCount())
    except Exception:
        return 0


def device_info(idx: int = 0) -> dict | None:
    """Static hardware info for the drill-down screen.

    Returns `{name, uuid, serial, pcie}` (any field may be `'n/a'`).
    `None` if NVML unavailable or `idx` is out of range. Each NVML
    call is individually fenced so partial-info drivers still surface
    what they can.
    """
    if not _init():
        return None
    try:
        from pynvml import (
            nvmlDeviceGetCurrPcieLinkGeneration,
            nvmlDeviceGetCurrPcieLinkWidth,
            nvmlDeviceGetHandleByIndex,
            nvmlDeviceGetMaxPcieLinkGeneration,
            nvmlDeviceGetMaxPcieLinkWidth,
            nvmlDeviceGetName,
            nvmlDeviceGetSerial,
            nvmlDeviceGetUUID,
        )

        h = nvmlDeviceGetHandleByIndex(idx)
    except Exception:
        return None

    def _try_str(fn):
        try:
            v = fn(h)
            if isinstance(v, bytes):
                v = v.decode(errors="replace")
            return str(v)
        except Exception:
            return None

    def _try(fn):
        try:
            return fn(h)
        except Exception:
            return None

    info = {
        "name":   _try_str(nvmlDeviceGetName) or "?",
        "uuid":   _try_str(nvmlDeviceGetUUID) or "n/a",
        "serial": _try_str(nvmlDeviceGetSerial) or "n/a",
    }
    cur_gen = _try(nvmlDeviceGetCurrPcieLinkGeneration)
    cur_w   = _try(nvmlDeviceGetCurrPcieLinkWidth)
    max_gen = _try(nvmlDeviceGetMaxPcieLinkGeneration)
    max_w   = _try(nvmlDeviceGetMaxPcieLinkWidth)
    if cur_gen and cur_w:
        pcie = f"gen{cur_gen} x{cur_w}"
        if max_gen and max_w and (cur_gen, cur_w) != (max_gen, max_w):
            pcie += f"  (max gen{max_gen} x{max_w})"
        info["pcie"] = pcie
    return info


def power_limit_info(idx: int = 0) -> dict | None:
    """Return current / min / max power-limit info (watts) for GPU `idx`.

    Returns None when NVML isn't available, the GPU doesn't exist, or
    the driver doesn't expose power-limit constraints.
    """
    if not _init():
        return None
    try:
        from pynvml import (
            nvmlDeviceGetHandleByIndex,
            nvmlDeviceGetName,
            nvmlDeviceGetPowerManagementLimit,
            nvmlDeviceGetPowerManagementLimitConstraints,
            nvmlDeviceGetPowerUsage,
        )

        h = nvmlDeviceGetHandleByIndex(idx)
        name = nvmlDeviceGetName(h)
        if isinstance(name, bytes):
            name = name.decode()
        try:
            cur_w = nvmlDeviceGetPowerUsage(h) / 1000.0
        except Exception:
            cur_w = 0.0
        cap_w = nvmlDeviceGetPowerManagementLimit(h) / 1000.0
        try:
            min_mw, max_mw = (
                nvmlDeviceGetPowerManagementLimitConstraints(h)
            )
            min_w = max(1, int(min_mw / 1000))
            max_w = int(max_mw / 1000)
        except Exception:
            # Constraint query not supported — fall back to a sane range
            min_w = 1
            max_w = int(max(cap_w * 2, cap_w + 50))
        return {
            "name": name,
            "current_w": cur_w,
            "cap_w": cap_w,
            "min_w": min_w,
            "max_w": max_w,
        }
    except Exception:
        return None


def set_power_limit(idx: int, watts: int) -> dict:
    """Apply a power-management limit (watts) to GPU `idx`.

    Returns {'ok': bool, 'message': str}. Requires admin / root on
    most systems — NVML returns NVML_ERROR_NO_PERMISSION otherwise,
    and we surface that as an error string.
    """
    if not _init():
        return {"ok": False, "message": "No NVIDIA driver / GPU"}
    try:
        from pynvml import (
            nvmlDeviceGetHandleByIndex,
            nvmlDeviceSetPowerManagementLimit,
        )

        h = nvmlDeviceGetHandleByIndex(idx)
        nvmlDeviceSetPowerManagementLimit(h, int(watts * 1000))
        return {
            "ok": True,
            "message": f"GPU{idx} power limit set to {watts} W",
        }
    except Exception as e:
        return {
            "ok": False,
            "message": f"Failed (admin needed?): {e}",
        }
