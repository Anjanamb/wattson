"""NVIDIA GPU probe via NVML.

Gracefully reports a one-liner when no NVIDIA driver/GPU is present so the
TUI panel never crashes. Future: AMD (ROCm/rocm-smi) and Apple Silicon
(powermetrics) backends behind the same `snapshot()` contract.
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


def snapshot() -> str:
    if not _init():
        return "No NVIDIA GPU / driver detected.\n(install driver and nvidia-ml-py)"
    try:
        from pynvml import (
            NVML_TEMPERATURE_GPU,
            nvmlDeviceGetCount,
            nvmlDeviceGetHandleByIndex,
            nvmlDeviceGetMemoryInfo,
            nvmlDeviceGetName,
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
            out.append(
                f"GPU{i}: {name}\n"
                f"  Util:  {util.gpu:3d}%   MemBW: {util.memory:3d}%\n"
                f"  VRAM:  {_gb(mem.used):5.1f} / {_gb(mem.total):5.1f} GB\n"
                f"  Temp:  {temp}°C"
            )
        return "\n\n".join(out)
    except Exception as e:
        return f"NVML error: {e}"
