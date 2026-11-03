"""`wattson diagnose` — sample the box for a few seconds, then explain it.

Built for two audiences:

  - humans: `wattson diagnose` prints a ranked list of findings, each with
    the evidence behind it and what to try next;
  - AI agents: `wattson diagnose --json` emits the same thing as a stable
    JSON document (schema version in `SCHEMA_VERSION`) so a coding agent
    can read it, correlate it with the user's training code, and propose
    fixes. The packaged agent skill in `skills/wattson-diagnose/` drives
    exactly this loop.

Pipeline (each stage is a plain function so it can be unit-tested with
synthetic data — no GPU needed):

    sample(duration, interval)  -> raw per-tick readings   (touches the OS)
    summarize(raw)              -> one JSON-friendly dict  (pure)
    analyze(summary)            -> list[Finding]           (pure)
    run(...)                    -> {"summary", "findings", ...}

Rules are deliberately conservative heuristics. Every finding carries the
numbers that triggered it (`evidence`) so an agent or human can sanity
check the conclusion instead of trusting it blindly.
"""

from __future__ import annotations

import platform
import time
from statistics import mean
from typing import Any, TypedDict

import psutil

from . import __version__
from .watchdog import (
    CPU_TEMP_CRIT,
    CPU_TEMP_WARN,
    GPU_TEMP_CRIT,
    GPU_TEMP_WARN,
    MEM_PCT_CRIT,
    MEM_PCT_WARN,
    VRAM_PCT_CRIT,
    VRAM_PCT_WARN,
)

SCHEMA_VERSION = 1

# ---- Thresholds (tunable; documented in skills/.../references/findings.md)
GPU_STARVED_UTIL = 60.0        # avg GPU util below this while a job holds VRAM
GPU_IDLE_UTIL = 5.0            # "basically doing nothing"
STALE_PROC_CPU = 1.0           # VRAM holder using <1% CPU on an idle GPU
CPU_SATURATED_PCT = 85.0       # whole-machine CPU busy
SINGLE_CORE_HOT_PCT = 90.0     # one core pegged ...
SINGLE_CORE_AVG_MAX = 50.0     # ... while the machine overall is not
IOWAIT_HIGH_PCT = 10.0
DISK_READ_HIGH_MB_S = 150.0
THROTTLE_FRACTION = 0.2        # throttled in >=20% of samples
SWAP_RATE_MB_S = 1.0
DISK_FULL_WARN = 90.0
DISK_FULL_CRIT = 97.0
DISK_MIN_SIZE_GB = 1.0         # ignore tiny/boot/loop volumes

_THERMAL_LABELS = {"Thermal (sw)", "Thermal (hw)", "HW slowdown"}
_POWER_LABELS = {"PowerCap (sw)", "Power brake"}
_SKIP_FSTYPES = {"squashfs", "tmpfs", "devtmpfs", "overlay", "iso9660", "ramfs"}

_SEVERITY_RANK = {"crit": 0, "warn": 1, "info": 2}


class Finding(TypedDict):
    id: str
    severity: str            # "crit" | "warn" | "info"
    title: str
    evidence: dict[str, Any]
    explanation: str
    suggestions: list[str]
    actions: list[dict[str, Any]]   # wattson commands an agent may *offer* to run


def _finding(
    id_: str,
    severity: str,
    title: str,
    evidence: dict[str, Any],
    explanation: str,
    suggestions: list[str],
    actions: list[dict[str, Any]] | None = None,
) -> Finding:
    return {
        "id": id_,
        "severity": severity,
        "title": title,
        "evidence": evidence,
        "explanation": explanation,
        "suggestions": suggestions,
        "actions": actions or [],
    }


def _r(x: float | None, nd: int = 1) -> float | None:
    return None if x is None else round(float(x), nd)


# ---------------------------------------------------------------------------
# Stage 1 — sampling (the only part that touches the OS)
# ---------------------------------------------------------------------------

def _gpu_reading() -> tuple[bool, dict[int, dict]]:
    from .probes import gpu

    data = gpu.devices()
    if data is None:
        return False, {}
    out: dict[int, dict] = {}
    for i, d in data.items():
        out[i] = dict(d)
        out[i]["throttle_labels"] = gpu.throttle_labels(d.get("throttle_mask") or 0)
    return True, out


def _cpu_temp() -> float | None:
    from .probes import cpu

    return cpu.metrics().get("cpu.temp")


def _disks() -> list[dict]:
    from .probes.disk import _disk_usage, _list_partitions

    seen: set[str] = set()
    out: list[dict] = []
    for p in _list_partitions():
        if p.fstype in _SKIP_FSTYPES or p.mountpoint in seen:
            continue
        if p.mountpoint.startswith(("/snap", "/boot/efi", "/var/snap")):
            continue
        try:
            u = _disk_usage(p.mountpoint)
        except Exception:
            continue
        total_gb = u.total / 1024**3
        if total_gb < DISK_MIN_SIZE_GB:
            continue
        seen.add(p.mountpoint)
        out.append({
            "mount": p.mountpoint,
            "fstype": p.fstype,
            "total_gb": _r(total_gb),
            "free_gb": _r(u.free / 1024**3),
            "used_pct": _r(u.percent),
        })
    return out


def sample(duration: float = 10.0, interval: float = 1.0) -> dict:
    """Collect raw readings for `duration` seconds at `interval` spacing.

    Returns {"ticks": [...], "processes": [...], "disks": [...],
    "elapsed_s": float, "gpu_available": bool, ...}. Only this function
    has side effects; everything downstream is pure.
    """
    from .probes import processes

    interval = max(0.2, float(interval))
    n = max(1, round(float(duration) / interval))

    # Prime the delta-based counters so the first tick is meaningful.
    psutil.cpu_percent(percpu=True)
    psutil.cpu_times_percent()
    processes.snapshot(limit=25)
    io0 = _safe(psutil.disk_io_counters)
    sw0 = _safe(psutil.swap_memory)
    t0 = time.monotonic()

    ticks: list[dict] = []
    gpu_available = False
    for _ in range(n):
        time.sleep(interval)
        per_core = psutil.cpu_percent(percpu=True)
        times = psutil.cpu_times_percent()
        vm = psutil.virtual_memory()
        gpu_available, gpus = _gpu_reading()
        ticks.append({
            "cpu_per_core": per_core,
            "iowait": getattr(times, "iowait", None),
            "mem_pct": vm.percent,
            "mem_avail_gb": vm.available / 1024**3,
            "gpus": gpus,
        })

    elapsed = time.monotonic() - t0
    io1 = _safe(psutil.disk_io_counters)
    sw1 = _safe(psutil.swap_memory)

    def _rate(a, b, attr) -> float | None:
        if a is None or b is None or elapsed <= 0:
            return None
        return (getattr(b, attr, 0) - getattr(a, attr, 0)) / 1024**2 / elapsed

    swap_rate = None
    if sw0 is not None and sw1 is not None:
        sin, sout = _rate(sw0, sw1, "sin"), _rate(sw0, sw1, "sout")
        swap_rate = (sin or 0.0) + (sout or 0.0)

    try:
        load1 = psutil.getloadavg()[0]
    except (AttributeError, OSError):
        load1 = None

    return {
        "elapsed_s": elapsed,
        "ticks": ticks,
        "gpu_available": gpu_available,
        "cpu_temp_c": _cpu_temp(),
        "load_avg_1m": load1,
        "swap_pct": sw1.percent if sw1 is not None else None,
        "swap_rate_mb_s": swap_rate,
        "disk_read_mb_s": _rate(io0, io1, "read_bytes"),
        "disk_write_mb_s": _rate(io0, io1, "write_bytes"),
        "disks": _disks(),
        "processes": processes.snapshot(limit=25),
    }


def _safe(fn):
    try:
        return fn()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Stage 2 — summarize raw ticks into one flat, JSON-friendly dict
# ---------------------------------------------------------------------------

def _avg(vals: list[float | None]) -> float | None:
    xs = [v for v in vals if v is not None]
    return mean(xs) if xs else None


def summarize(raw: dict) -> dict:
    ticks = raw.get("ticks") or []
    cores = [t["cpu_per_core"] for t in ticks if t.get("cpu_per_core")]
    ncores = len(cores[0]) if cores else psutil.cpu_count() or 0

    cpu_avg = _avg([mean(c) for c in cores]) if cores else None
    hottest_core = _avg([max(c) for c in cores]) if cores else None

    gpu_ids = sorted({i for t in ticks for i in (t.get("gpus") or {})})
    gpus: list[dict] = []
    for i in gpu_ids:
        rows = [t["gpus"][i] for t in ticks if i in (t.get("gpus") or {})]
        last = rows[-1]
        utils = [r.get("util") for r in rows if r.get("util") is not None]
        temps = [r.get("temp") for r in rows if r.get("temp") is not None]
        label_hits: dict[str, int] = {}
        for r in rows:
            for lab in r.get("throttle_labels") or []:
                label_hits[lab] = label_hits.get(lab, 0) + 1
        vu, vt = last.get("vram_used"), last.get("vram_total")
        gpus.append({
            "index": i,
            "name": last.get("name"),
            "util_avg": _r(_avg(utils)),
            "util_min": _r(min(utils)) if utils else None,
            "util_max": _r(max(utils)) if utils else None,
            "mem_bw_avg": _r(_avg([r.get("mem_bw") for r in rows])),
            "vram_used_mb": _r(vu / 1024**2) if vu is not None else None,
            "vram_total_mb": _r(vt / 1024**2) if vt is not None else None,
            "vram_pct": _r(100.0 * vu / vt) if vu is not None and vt else None,
            "temp_max_c": _r(max(temps)) if temps else None,
            "power_avg_w": _r(_avg([r.get("power_w") for r in rows])),
            "power_cap_w": _r(last.get("power_cap_w")),
            "throttle_fraction": {
                k: round(v / len(rows), 2) for k, v in sorted(label_hits.items())
            },
        })

    procs = []
    for p in raw.get("processes") or []:
        procs.append({
            "pid": p["pid"],
            "name": p["name"],
            "cpu_pct": _r(p["cpu_pct"]),
            "mem_mb": _r(p["mem_mb"], 0),
            "gpu_idx": p.get("gpu_idx"),
            "vram_mb": _r(p.get("vram_mb"), 0),
            "cmdline": p.get("cmdline", ""),
        })

    return {
        "duration_s": _r(raw.get("elapsed_s")),
        "samples": len(ticks),
        "cpu": {
            "logical_cores": ncores,
            "avg_pct": _r(cpu_avg),
            "hottest_core_avg_pct": _r(hottest_core),
            "iowait_pct": _r(_avg([t.get("iowait") for t in ticks])),
            "temp_c": _r(raw.get("cpu_temp_c")),
            "load_avg_1m": _r(raw.get("load_avg_1m"), 2),
        },
        "memory": {
            "ram_pct": _r(_avg([t.get("mem_pct") for t in ticks])),
            "ram_avail_gb": _r(_avg([t.get("mem_avail_gb") for t in ticks])),
            "swap_pct": _r(raw.get("swap_pct")),
            "swap_rate_mb_s": _r(raw.get("swap_rate_mb_s"), 2),
        },
        "disk_io": {
            "read_mb_s": _r(raw.get("disk_read_mb_s")),
            "write_mb_s": _r(raw.get("disk_write_mb_s")),
        },
        "gpu_available": bool(raw.get("gpu_available")),
        "gpus": gpus,
        "disks": raw.get("disks") or [],
        "processes": procs,
    }


# ---------------------------------------------------------------------------
# Stage 3 — rules
# ---------------------------------------------------------------------------

def _gpu_holders(summary: dict, idx: int) -> list[dict]:
    return [
        p for p in summary.get("processes", [])
        if p.get("gpu_idx") == idx and (p.get("vram_mb") or 0) > 0
    ]


def _rule_no_gpu(s: dict) -> list[Finding]:
    if s.get("gpu_available"):
        return []
    return [_finding(
        "no_gpu", "info", "No NVIDIA GPU visible via NVML",
        {"gpu_available": False},
        "wattson could not talk to an NVIDIA driver, so GPU checks were "
        "skipped. CPU, memory and disk checks still ran.",
        [
            "If you expected a GPU: check `nvidia-smi` works in this same shell/container.",
            "Inside Docker, run with `--gpus all`; inside WSL2, install the Windows NVIDIA driver (not a Linux one).",
            "AMD / Apple Silicon GPUs are not supported yet.",
        ],
    )]


def _rule_stale_and_starved(s: dict) -> list[Finding]:
    out: list[Finding] = []
    cpu = s["cpu"]
    for g in s.get("gpus", []):
        util = g.get("util_avg")
        if util is None:
            continue
        holders = _gpu_holders(s, g["index"])
        held = holders or (g.get("vram_pct") or 0) >= 10.0
        if not held:
            continue

        # Idle GPU + VRAM held by a process that isn't doing anything:
        # a leftover notebook kernel / crashed run keeping memory pinned.
        if util < GPU_IDLE_UTIL and holders:
            stale = [p for p in holders if (p.get("cpu_pct") or 0) < STALE_PROC_CPU]
            if stale:
                out.append(_finding(
                    "stale_gpu_process", "warn",
                    f"GPU{g['index']}: idle process(es) holding VRAM",
                    {"gpu": g["index"], "gpu_util_avg": util,
                     "processes": [{k: p[k] for k in ("pid", "name", "vram_mb", "cpu_pct", "cmdline")}
                                   for p in stale]},
                    "The GPU is idle but these processes still hold VRAM and use "
                    "almost no CPU. Typical culprits: an idle Jupyter kernel, a "
                    "finished-but-not-exited script, or a hung DataLoader worker.",
                    [
                        "Confirm with the user the process is not needed (e.g. an open notebook).",
                        "In notebooks: `del model; torch.cuda.empty_cache()` or restart the kernel.",
                    ],
                    [{"command": f"wattson kill {p['pid']}",
                      "effect": f"SIGTERM {p['name']} (PID {p['pid']})",
                      "requires_confirmation": True} for p in stale],
                ))
                continue

        if util >= GPU_STARVED_UTIL or not holders:
            continue

        ev = {
            "gpu": g["index"], "gpu_util_avg": util,
            "gpu_util_min": g.get("util_min"), "gpu_util_max": g.get("util_max"),
            "cpu_avg_pct": cpu.get("avg_pct"),
            "hottest_core_avg_pct": cpu.get("hottest_core_avg_pct"),
            "iowait_pct": cpu.get("iowait_pct"),
            "disk_read_mb_s": s["disk_io"].get("read_mb_s"),
            "gpu_processes": [{"pid": p["pid"], "name": p["name"],
                               "cpu_pct": p["cpu_pct"]} for p in holders],
        }
        iowait = cpu.get("iowait_pct") or 0.0
        read = s["disk_io"].get("read_mb_s") or 0.0
        cpu_avg = cpu.get("avg_pct") or 0.0
        hottest = cpu.get("hottest_core_avg_pct") or 0.0

        if iowait >= IOWAIT_HIGH_PCT or read >= DISK_READ_HIGH_MB_S:
            out.append(_finding(
                "gpu_starved_io", "warn",
                f"GPU{g['index']} waiting on disk I/O ({util:.0f}% util)",
                ev,
                "The GPU is under-used while the CPU spends a lot of time "
                "waiting on storage. The input pipeline is reading data "
                "faster than the disk can deliver it.",
                [
                    "Move the dataset to local NVMe (not a network/USB drive or the WSL /mnt/c bridge).",
                    "Cache decoded samples, or pack small files into shards (WebDataset, TFRecord, LMDB).",
                    "Raise DataLoader `prefetch_factor` and use `persistent_workers=True`.",
                ],
            ))
        elif cpu_avg >= CPU_SATURATED_PCT:
            out.append(_finding(
                "gpu_starved_cpu", "warn",
                f"GPU{g['index']} starved by CPU-bound input pipeline ({util:.0f}% util)",
                ev,
                "All CPU cores are busy while the GPU waits. Data loading / "
                "augmentation / tokenization on the CPU cannot keep up.",
                [
                    "Move augmentation to the GPU (torchvision.transforms.v2 on CUDA tensors, DALI, Kornia).",
                    "Pre-process / pre-tokenize once and save to disk instead of every epoch.",
                    "Check `num_workers` isn't larger than the physical cores (oversubscription).",
                    "Lower-cost decoding: smaller JPEG sizes, pillow-simd, or decode on GPU (nvjpeg).",
                ],
            ))
        elif hottest >= SINGLE_CORE_HOT_PCT and cpu_avg < SINGLE_CORE_AVG_MAX:
            out.append(_finding(
                "gpu_starved_single_thread", "warn",
                f"GPU{g['index']} starved by a single busy CPU thread ({util:.0f}% util)",
                ev,
                "One CPU core is pegged while the rest of the machine is "
                "idle. Classic signature of `DataLoader(num_workers=0)` or "
                "Python-side preprocessing running in the main process.",
                [
                    "Set `DataLoader(num_workers=N)` with N ≈ physical cores − 1, plus `pin_memory=True`.",
                    "Move per-sample Python work out of the training loop into the Dataset.",
                    "On Windows/macOS, guard the entry point with `if __name__ == '__main__':` so workers can spawn.",
                ],
            ))
        else:
            out.append(_finding(
                "gpu_underutilized", "info",
                f"GPU{g['index']} under-used ({util:.0f}% util) without an obvious CPU/I/O bottleneck",
                ev,
                "Neither the CPU nor the disk looks saturated, so the GPU is "
                "probably waiting on synchronisation or doing too little work "
                "per step.",
                [
                    "Increase batch size (or use gradient accumulation) if VRAM allows.",
                    "Remove per-step host syncs: `.item()`, `.cpu()`, `print(loss)` every step.",
                    "Try mixed precision (`torch.autocast`) and `torch.compile`.",
                    "If training is short, this may just be eval/checkpoint phases — re-run diagnose mid-epoch.",
                ],
            ))
    return out


def _rule_throttle(s: dict) -> list[Finding]:
    out: list[Finding] = []
    for g in s.get("gpus", []):
        frac = g.get("throttle_fraction") or {}
        thermal = max((v for k, v in frac.items() if k in _THERMAL_LABELS), default=0.0)
        power = max((v for k, v in frac.items() if k in _POWER_LABELS), default=0.0)
        temp = g.get("temp_max_c")
        hot = temp is not None and temp >= GPU_TEMP_WARN

        if thermal >= THROTTLE_FRACTION or hot:
            sev = "crit" if (temp or 0) >= GPU_TEMP_CRIT else "warn"
            out.append(_finding(
                "gpu_thermal_throttle", sev,
                f"GPU{g['index']} running hot / thermally throttled",
                {"gpu": g["index"], "temp_max_c": temp,
                 "thermal_throttle_fraction": thermal, "reasons": frac},
                "The GPU is slowing its clocks to protect itself from heat. "
                "Training still works but runs slower than it should.",
                [
                    "Improve airflow: laptop on a hard surface or stand, clean fans/filters.",
                    "Lower the power limit a little — often costs <5% speed for a big temperature drop.",
                    "Check fan curves in the vendor tool (e.g. Armoury Crate, MSI Afterburner).",
                ],
                [{"command": f"wattson power <watts> --gpu {g['index']}",
                  "effect": "lower the GPU power limit (needs admin/root)",
                  "requires_confirmation": True}],
            ))
        if power >= THROTTLE_FRACTION and (g.get("util_avg") or 0) >= GPU_STARVED_UTIL:
            out.append(_finding(
                "gpu_power_capped", "info",
                f"GPU{g['index']} is at its power limit",
                {"gpu": g["index"], "power_avg_w": g.get("power_avg_w"),
                 "power_cap_w": g.get("power_cap_w"), "power_throttle_fraction": power},
                "The GPU is busy and drawing its maximum allowed power. That is "
                "normal under full load; it only matters if the cap was lowered "
                "on purpose or by a laptop power profile.",
                [
                    "On laptops: plug in AC power and pick the performance power profile.",
                    "Compare `power_cap_w` with the card's default (see `wattson tui` → power modal).",
                ],
            ))
    return out


def _rule_vram(s: dict) -> list[Finding]:
    out: list[Finding] = []
    for g in s.get("gpus", []):
        pct = g.get("vram_pct")
        if pct is None or pct < VRAM_PCT_WARN:
            continue
        out.append(_finding(
            "vram_near_full", "crit" if pct >= VRAM_PCT_CRIT else "warn",
            f"GPU{g['index']} VRAM {pct:.0f}% full — OOM risk",
            {"gpu": g["index"], "vram_pct": pct, "vram_used_mb": g.get("vram_used_mb"),
             "vram_total_mb": g.get("vram_total_mb"),
             "holders": [{"pid": p["pid"], "name": p["name"], "vram_mb": p["vram_mb"]}
                         for p in _gpu_holders(s, g["index"])]},
            "VRAM is nearly exhausted. A longer sequence, a bigger eval batch "
            "or memory fragmentation can tip this into `CUDA out of memory`. "
            "(PyTorch's caching allocator also reserves memory up-front, so "
            "high usage alone is not proof of a problem.)",
            [
                "Use mixed precision (bf16/fp16) and/or gradient checkpointing.",
                "Reduce batch size and keep the effective batch with gradient accumulation.",
                "Set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` to reduce fragmentation.",
                "Make sure no second process (e.g. a notebook) shares this GPU.",
            ],
        ))
    return out


def _rule_contention(s: dict) -> list[Finding]:
    out: list[Finding] = []
    for g in s.get("gpus", []):
        holders = _gpu_holders(s, g["index"])
        if len(holders) < 2:
            continue
        out.append(_finding(
            "gpu_contention", "info",
            f"GPU{g['index']} shared by {len(holders)} processes",
            {"gpu": g["index"],
             "processes": [{"pid": p["pid"], "name": p["name"], "vram_mb": p["vram_mb"],
                            "cmdline": p["cmdline"]} for p in holders]},
            "Several processes hold memory on the same GPU. They compete for "
            "compute and VRAM, which slows each of them down.",
            ["Check every process is intentional; stop leftovers (old notebooks, eval scripts)."],
        ))
    return out


def _rule_memory(s: dict) -> list[Finding]:
    out: list[Finding] = []
    m = s["memory"]
    ram = m.get("ram_pct")
    if ram is not None and ram >= MEM_PCT_WARN:
        top = sorted(s.get("processes", []), key=lambda p: -(p.get("mem_mb") or 0))[:3]
        out.append(_finding(
            "ram_pressure", "crit" if ram >= MEM_PCT_CRIT else "warn",
            f"System RAM {ram:.0f}% used",
            {"ram_pct": ram, "ram_avail_gb": m.get("ram_avail_gb"),
             "top_memory_processes": [{"pid": p["pid"], "name": p["name"],
                                       "mem_mb": p["mem_mb"]} for p in top]},
            "The machine is close to running out of RAM; the OS may start "
            "swapping or the OOM killer may end the training run.",
            [
                "Each DataLoader worker copies the Dataset — avoid big Python lists/dicts in it (use numpy arrays or memmap).",
                "Lower `num_workers` or `prefetch_factor`.",
                "Load data lazily from disk instead of holding the whole dataset in memory.",
            ],
        ))
    rate = m.get("swap_rate_mb_s")
    if rate is not None and rate >= SWAP_RATE_MB_S:
        out.append(_finding(
            "swapping", "warn",
            f"Actively swapping ({rate:.1f} MB/s)",
            {"swap_rate_mb_s": rate, "swap_pct": m.get("swap_pct"), "ram_pct": ram},
            "Memory pages are moving to/from disk right now. Everything, "
            "including the input pipeline, slows down dramatically.",
            ["Reduce memory use (see `ram_pressure` suggestions) or add RAM."],
        ))
    return out


def _rule_disk(s: dict) -> list[Finding]:
    out: list[Finding] = []
    for d in s.get("disks", []):
        pct = d.get("used_pct")
        if pct is None or pct < DISK_FULL_WARN:
            continue
        out.append(_finding(
            "disk_nearly_full", "crit" if pct >= DISK_FULL_CRIT else "warn",
            f"Disk {d['mount']} {pct:.0f}% full ({d.get('free_gb')} GB free)",
            {"mount": d["mount"], "used_pct": pct, "free_gb": d.get("free_gb")},
            "Checkpoints, logs and dataset caches can fill the remaining "
            "space and crash a run at save time.",
            [
                "Keep only the last/best N checkpoints (e.g. `save_total_limit`).",
                "Clean caches: `~/.cache/huggingface`, `~/.cache/pip`, `conda clean -a`, old `wandb/` runs.",
            ],
        ))
    return out


def _rule_cpu_temp(s: dict) -> list[Finding]:
    t = s["cpu"].get("temp_c")
    if t is None or t < CPU_TEMP_WARN:
        return []
    return [_finding(
        "cpu_hot", "crit" if t >= CPU_TEMP_CRIT else "warn",
        f"CPU at {t:.0f}°C",
        {"cpu_temp_c": t, "cpu_avg_pct": s["cpu"].get("avg_pct")},
        "The CPU is near its thermal limit and will throttle, slowing "
        "data loading.",
        ["Improve cooling / airflow; reduce `num_workers` if the CPU is the bottleneck anyway."],
    )]


RULES = (
    _rule_no_gpu,
    _rule_stale_and_starved,
    _rule_throttle,
    _rule_vram,
    _rule_contention,
    _rule_memory,
    _rule_disk,
    _rule_cpu_temp,
)


def analyze(summary: dict) -> list[Finding]:
    findings: list[Finding] = []
    for rule in RULES:
        findings.extend(rule(summary))
    findings.sort(key=lambda f: _SEVERITY_RANK.get(f["severity"], 9))
    if not any(f["severity"] in ("warn", "crit") for f in findings):
        findings.append(_finding(
            "healthy", "info", "No problems detected",
            {"gpus": len(summary.get("gpus", [])),
             "duration_s": summary.get("duration_s")},
            "Nothing crossed a warning threshold during the sample window.",
            ["If a run is slow only sometimes, re-run diagnose while it is slow, "
             "or check `wattson events` for past alerts."],
        ))
    return findings


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def run(duration: float = 10.0, interval: float = 1.0) -> dict:
    summary = summarize(sample(duration, interval))
    return report(summary)


def report(summary: dict) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "wattson_version": __version__,
        "host": {"platform": platform.platform(), "python": platform.python_version()},
        "findings": analyze(summary),
        "summary": summary,
    }


def render_text(rep: dict) -> str:
    """Rich-markup rendering for humans."""
    from rich.markup import escape

    colour = {"crit": "bold red", "warn": "yellow", "info": "cyan"}
    s = rep["summary"]
    lines = [
        f"[bold]wattson diagnose[/bold] · {s['samples']} samples over {s['duration_s']} s",
        "",
    ]
    for f in rep["findings"]:
        c = colour.get(f["severity"], "white")
        lines.append(f"[{c}]{f['severity'].upper():<4}[/{c}] [bold]{escape(f['title'])}[/bold]  [dim]({f['id']})[/dim]")
        lines.append(f"     {escape(f['explanation'])}")
        for sug in f["suggestions"]:
            lines.append(f"     • {escape(sug)}")
        for a in f["actions"]:
            lines.append(f"     [dim]→ {escape(a['command'])}  ({escape(a['effect'])})[/dim]")
        lines.append("")
    return "\n".join(lines).rstrip()
