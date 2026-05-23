"""Process probe — top processes by resource use, with GPU attribution.

Unlike the v0 string-returning probes, this one returns structured rows
so the TUI can render them in a DataTable. Reasoning is documented in
the parent CLAUDE.md (the probe contract is allowed to evolve from
`snapshot() -> str` to `snapshot() -> structured` when state or
structure is needed; this probe needs both).

State carried across snapshots:
  - psutil.Process cache, so `cpu_percent()` deltas are meaningful from
    the second call onwards (the first call after construction always
    returns 0.0 — that's psutil's contract, not a bug).
"""

from __future__ import annotations

from typing import Optional, TypedDict

import psutil

from .gpu import _init as _gpu_init


class ProcessRow(TypedDict):
    pid: int
    name: str
    cpu_pct: float
    mem_mb: float
    gpu_idx: Optional[int]
    vram_mb: Optional[float]
    cmdline: str


class ProcessProbe:
    def __init__(self) -> None:
        # Bootstrap a baseline cpu_percent reading for every currently
        # running process; without this, the first snapshot would show
        # everything at 0%.
        self._procs: dict[int, psutil.Process] = {}
        for p in psutil.process_iter():
            try:
                p.cpu_percent()
                self._procs[p.pid] = p
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue

    def _collect_gpu_processes(self) -> dict[int, tuple[int, float]]:
        """Return {pid: (gpu_idx, vram_mb)} for processes holding VRAM."""
        if not _gpu_init():
            return {}
        try:
            from pynvml import (
                nvmlDeviceGetComputeRunningProcesses,
                nvmlDeviceGetCount,
                nvmlDeviceGetHandleByIndex,
            )
        except Exception:
            return {}

        out: dict[int, tuple[int, float]] = {}
        try:
            n = nvmlDeviceGetCount()
        except Exception:
            return {}

        for i in range(n):
            try:
                h = nvmlDeviceGetHandleByIndex(i)
                for p in nvmlDeviceGetComputeRunningProcesses(h):
                    # usedGpuMemory is None on some drivers; treat as 0
                    vram_mb = (getattr(p, "usedGpuMemory", None) or 0) / (1024 * 1024)
                    # If a process spans multiple GPUs, keep the largest
                    prev = out.get(p.pid)
                    if prev is None or vram_mb > prev[1]:
                        out[p.pid] = (i, vram_mb)
            except Exception:
                continue
        return out

    def snapshot(self, limit: int = 15) -> list[ProcessRow]:
        gpu_procs = self._collect_gpu_processes()

        current: dict[int, psutil.Process] = {}
        rows: list[ProcessRow] = []
        for proc in psutil.process_iter():
            try:
                pid = proc.pid
                # Reuse the cached Process when possible so cpu_percent()
                # has the right baseline.
                cached = self._procs.get(pid)
                if cached is not None:
                    proc = cached
                current[pid] = proc

                with proc.oneshot():
                    name = proc.name() or "?"
                    cpu_pct = proc.cpu_percent()
                    mem_mb = proc.memory_info().rss / (1024 * 1024)
                    cmd = proc.cmdline()
                cmdline = " ".join(cmd[:6])[:80] if cmd else name

                gpu_info = gpu_procs.get(pid)
                gpu_idx = gpu_info[0] if gpu_info else None
                vram_mb = gpu_info[1] if gpu_info else None

                rows.append(
                    {
                        "pid": pid,
                        "name": name,
                        "cpu_pct": cpu_pct,
                        "mem_mb": mem_mb,
                        "gpu_idx": gpu_idx,
                        "vram_mb": vram_mb,
                        "cmdline": cmdline,
                    }
                )
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue

        # Forget cached processes that no longer exist
        self._procs = current

        # GPU processes first (sorted by VRAM desc), then everyone else by CPU%
        rows.sort(
            key=lambda r: (
                r["vram_mb"] is None,           # False (has VRAM) sorts before True
                -(r["vram_mb"] or 0.0),
                -r["cpu_pct"],
                -r["mem_mb"],
            )
        )
        return rows[:limit]


_probe: ProcessProbe | None = None


def snapshot(limit: int = 15) -> list[ProcessRow]:
    """Module-level entry point; lazily constructs a singleton probe."""
    global _probe
    if _probe is None:
        _probe = ProcessProbe()
    return _probe.snapshot(limit)
