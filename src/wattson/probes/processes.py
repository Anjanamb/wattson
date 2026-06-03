"""Process probe — top processes by resource use, with GPU attribution
and criticality flagging.

Unlike the v0 string-returning probes, this one returns structured rows
so the TUI can render them in a DataTable. Reasoning is documented in
the parent CLAUDE.md (the probe contract is allowed to evolve from
`snapshot() -> str` to `snapshot() -> structured` when state or
structure is needed; this probe needs both).

State carried across snapshots:
  - psutil.Process cache, so `cpu_percent()` deltas are meaningful from
    the second call onwards (the first call after construction always
    returns 0.0 — that's psutil's contract, not a bug).

Criticality (v0.0.4): a row is flagged `critical: True` if it
  - holds any VRAM (almost certainly a training/inference workload), OR
  - has CPU% > 50 and is not one of the OS "idle"/scheduler PIDs, OR
  - holds > 10% of system memory.

Also exposes `terminate(pid)` for the TUI's interactive kill action.
"""

from __future__ import annotations

from typing import Optional, TypedDict

import psutil

from .gpu import _init as _gpu_init


# Process names that show up as "high CPU" but aren't real workloads.
_IDLE_NAMES = {
    "System Idle Process",
    "System",
    "Idle",
    "kernel_task",
    "swapper",
    "kworker",
}


class ProcessRow(TypedDict):
    pid: int
    name: str
    cpu_pct: float
    mem_mb: float
    gpu_idx: Optional[int]
    vram_mb: Optional[float]
    cmdline: str
    critical: bool


class TerminateResult(TypedDict):
    ok: bool
    message: str


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
                    raw = getattr(p, "usedGpuMemory", None) or 0
                    vram_mb = raw / (1024 * 1024)
                    # If a process spans multiple GPUs, keep the largest
                    prev = out.get(p.pid)
                    if prev is None or vram_mb > prev[1]:
                        out[p.pid] = (i, vram_mb)
            except Exception:
                continue
        return out

    def snapshot(self, limit: int = 15) -> list[ProcessRow]:
        gpu_procs = self._collect_gpu_processes()
        total_mem_mb = psutil.virtual_memory().total / (1024 * 1024)

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

                holds_vram = vram_mb is not None and vram_mb > 0.0
                is_idle = name in _IDLE_NAMES
                high_cpu = cpu_pct > 50.0 and not is_idle
                high_mem = total_mem_mb > 0 and (mem_mb / total_mem_mb) > 0.10
                critical = holds_vram or high_cpu or high_mem

                rows.append(
                    {
                        "pid": pid,
                        "name": name,
                        "cpu_pct": cpu_pct,
                        "mem_mb": mem_mb,
                        "gpu_idx": gpu_idx,
                        "vram_mb": vram_mb,
                        "cmdline": cmdline,
                        "critical": critical,
                    }
                )
            except (
                psutil.NoSuchProcess,
                psutil.AccessDenied,
                psutil.ZombieProcess,
            ):
                continue

        # Forget cached processes that no longer exist
        self._procs = current

        # GPU processes first (sorted by VRAM desc), then everyone else by CPU%
        rows.sort(
            key=lambda r: (
                r["vram_mb"] is None,    # False (has VRAM) sorts first
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


def terminate(pid: int) -> TerminateResult:
    """Best-effort terminate. Returns a structured result for the TUI to
    surface via Toast — never raises."""
    try:
        psutil.Process(pid).terminate()
        return {"ok": True, "message": f"Sent SIGTERM to PID {pid}"}
    except psutil.NoSuchProcess:
        return {"ok": False, "message": f"PID {pid} no longer exists"}
    except psutil.AccessDenied:
        msg = f"Access denied for PID {pid} (run as admin?)"
        return {"ok": False, "message": msg}
    except Exception as e:
        return {"ok": False, "message": f"Failed to terminate {pid}: {e}"}
