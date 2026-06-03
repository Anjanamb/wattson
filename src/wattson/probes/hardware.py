"""Hardware inventory — driver version, PCIe lanes, board serial, CPU
cache + flags, OS / kernel / Python. Static-ish info you ask about once.

Exposes `report() -> str` returning a multi-section Rich-markup string,
rendered by the `HardwareScreen` (pushed via the `i` keybinding).

Some NVML calls (Serial, UUID on some boards) require admin privileges
and return a permission error otherwise — those cases degrade to "n/a"
rather than failing the whole report.
"""

from __future__ import annotations

import platform
import sys

import psutil

from .gpu import _init as _gpu_init


def _human_bytes(b: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    i = 0
    while b >= 1024 and i < len(units) - 1:
        b /= 1024
        i += 1
    return f"{b:.1f} {units[i]}"


def _try(fn, *args):
    try:
        return fn(*args)
    except Exception:
        return None


def _try_str(fn, *args) -> str | None:
    v = _try(fn, *args)
    if v is None:
        return None
    if isinstance(v, bytes):
        v = v.decode(errors="replace")
    return str(v)


def _system_section() -> list[str]:
    sysmem = psutil.virtual_memory().total
    return [
        "[bold cyan]System[/bold cyan]",
        f"  OS:        {platform.system()} {platform.release()}",
        f"  Kernel:    {platform.version()[:64]}",
        f"  Arch:      {platform.machine()}",
        f"  Hostname:  {platform.node()}",
        f"  Memory:    {_human_bytes(sysmem)}",
        f"  Python:    {platform.python_version()}",
        f"  Exec:      {sys.executable}",
    ]


def _cpu_section() -> list[str]:
    out = ["[bold cyan]CPU[/bold cyan]"]
    brand = "Unknown"
    arch = "?"
    flags: list[str] = []
    cache_l1d = cache_l1i = cache_l2 = cache_l3 = "?"
    try:
        import cpuinfo

        ci = cpuinfo.get_cpu_info()
        brand = ci.get("brand_raw", brand)
        arch = ci.get("arch", arch)
        flags = ci.get("flags", []) or []
        cache_l1d = ci.get("l1_data_cache_size", "?")
        cache_l1i = ci.get("l1_instruction_cache_size", "?")
        cache_l2 = ci.get("l2_cache_size", "?")
        cache_l3 = ci.get("l3_cache_size", "?")
    except Exception:
        pass

    out.append(f"  Model:     {brand}")
    out.append(f"  Arch:      {arch}")
    out.append(
        f"  Cores:     {psutil.cpu_count(logical=False)} physical / "
        f"{psutil.cpu_count(logical=True)} logical"
    )
    out.append(f"  L1 cache:  {cache_l1d} (data), {cache_l1i} (instr)")
    out.append(f"  L2 cache:  {cache_l2}")
    out.append(f"  L3 cache:  {cache_l3}")
    # Surface only the flags that actually matter for ML / numerical workloads
    notable = [f for f in ("avx", "avx2", "avx512f", "sse4_2", "aes", "sha_ni") if f in flags]
    if notable:
        out.append(f"  Flags:     {', '.join(notable)}")
    return out


def _gpu_section() -> list[str]:
    out = ["[bold cyan]GPU(s)[/bold cyan]"]
    if not _gpu_init():
        out.append("  (no NVIDIA driver / GPU detected)")
        return out
    try:
        from pynvml import (
            nvmlDeviceGetCount,
            nvmlDeviceGetCurrPcieLinkGeneration,
            nvmlDeviceGetCurrPcieLinkWidth,
            nvmlDeviceGetHandleByIndex,
            nvmlDeviceGetMaxPcieLinkGeneration,
            nvmlDeviceGetMaxPcieLinkWidth,
            nvmlDeviceGetMemoryInfo,
            nvmlDeviceGetName,
            nvmlDeviceGetPciInfo,
            nvmlDeviceGetSerial,
            nvmlDeviceGetUUID,
            nvmlSystemGetCudaDriverVersion,
            nvmlSystemGetDriverVersion,
        )

        driver = _try_str(nvmlSystemGetDriverVersion)
        if driver:
            out.append(f"  Driver:    {driver}")
        cuda_raw = _try(nvmlSystemGetCudaDriverVersion)
        if cuda_raw:
            # cuda_raw is e.g. 12020 → "12.2"
            major = cuda_raw // 1000
            minor = (cuda_raw % 1000) // 10
            out.append(f"  CUDA:      {major}.{minor}")

        n = _try(nvmlDeviceGetCount) or 0
        for i in range(n):
            h = _try(nvmlDeviceGetHandleByIndex, i)
            if h is None:
                continue
            name = _try_str(nvmlDeviceGetName, h) or "?"
            uuid = _try_str(nvmlDeviceGetUUID, h) or "?"
            serial = _try_str(nvmlDeviceGetSerial, h) or "n/a"
            mem = _try(nvmlDeviceGetMemoryInfo, h)

            pci = _try(nvmlDeviceGetPciInfo, h)
            bus = None
            if pci is not None:
                bus_raw = getattr(pci, "busId", None) or getattr(pci, "bus_id", None)
                if isinstance(bus_raw, bytes):
                    bus_raw = bus_raw.decode(errors="replace")
                bus = bus_raw

            cur_gen = _try(nvmlDeviceGetCurrPcieLinkGeneration, h)
            cur_w = _try(nvmlDeviceGetCurrPcieLinkWidth, h)
            max_gen = _try(nvmlDeviceGetMaxPcieLinkGeneration, h)
            max_w = _try(nvmlDeviceGetMaxPcieLinkWidth, h)

            out.append("")
            out.append(f"  [bold]GPU{i}: {name}[/bold]")
            out.append(f"    UUID:    {uuid}")
            out.append(f"    Serial:  {serial}")
            if bus:
                out.append(f"    PCI bus: {bus}")
            if mem is not None:
                out.append(f"    VRAM:    {_human_bytes(mem.total)}")
            if cur_gen and cur_w:
                pcie = f"gen{cur_gen} x{cur_w}"
                if max_gen and max_w and (cur_gen != max_gen or cur_w != max_w):
                    pcie += f"  (max gen{max_gen} x{max_w})"
                out.append(f"    PCIe:    {pcie}")
    except Exception as e:
        out.append(f"  (NVML error: {e})")
    return out


def report() -> str:
    """Multi-section hardware inventory as a Rich-markup string."""
    sections = [
        "\n".join(_system_section()),
        "\n".join(_cpu_section()),
        "\n".join(_gpu_section()),
    ]
    return "\n\n".join(sections)
