# wattson — Claude project notes

DL-workload-aware system monitor. TUI built on `textual`. Started 2026-05-23.

Parent workspace: `Goals\github\CLAUDE.md` (gh CLI auth, conventions).

## Current state

`v0.0.5` — same as v0.0.4 plus a Trends screen with live sparklines for CPU / memory / per-GPU util / temp / power over the last 60 seconds. Repo is **public** (`Anjanamb/wattson`) as of 2026-05-27 — user pinned it on their profile alongside the rest of the showcase work.

### v0.0.5 additions

- **New module** `src/wattson/history.py` — `History` ring-buffer class with auto-create-on-first-write semantics so probes don't have to register. Module-level singleton `HISTORY` with capacity = 60 (= 1 minute at the 1 Hz refresh).
- **New probe entrypoint** `metrics() -> dict[str, float]` on each of cpu / memory / gpu. Returns the scalar values that the TrendsScreen plots. Keys: `cpu.pct`, `cpu.temp` (if available), `mem.pct`, `swap.pct`, and per-GPU `gpu{i}.util`, `gpu{i}.mem_bw`, `gpu{i}.temp`, `gpu{i}.power`. Each call individually try-fenced so a partial driver still surfaces what it can.
- **New screen** `TrendsScreen` in `app.py` — pushed via the new `t` keybinding. Renders one Textual `Sparkline` per metric inside a `ScrollableContainer`; per-GPU series count is determined by `gpu.device_count()` at compose time. The screen has its own 1 Hz `set_interval` so sparklines redraw smoothly while the parent app keeps populating `HISTORY` in the background.
- **History wiring** — `_refresh_all()` in `WattsonApp` calls `HISTORY.add_many(probe.metrics())` for cpu / memory / gpu each tick. Best-effort: probes don't have to know the buffer exists.
- **Lint pass** — folded several pre-existing > 79-char lines in cpu / memory / gpu probes that the IDE's default pycodestyle was flagging. Project's ruff config still allows 100, but staying under 79 keeps both linters quiet.

### v0.0.4 additions

- **New probe** `src/wattson/probes/hardware.py` — `report()` returns a Rich-markup multi-section string (System / CPU / GPU). Pulls driver + CUDA version (`nvmlSystemGetDriverVersion`, `nvmlSystemGetCudaDriverVersion`), per-GPU UUID + serial (`nvmlDeviceGetUUID` / `GetSerial` — serial often n/a without admin), PCIe gen + width (current vs max), CPU cache sizes + notable flags (avx, avx2, avx512f, sha_ni), OS / kernel / Python info. Each NVML call individually `_try`-fenced for partial-info drivers.
- **New screen** `HardwareScreen` in `app.py` — full-screen, scrollable, pushed by the new `i` keybinding. `Esc` / `q` / `i` all pop back to the dashboard.
- **Process criticality** — `ProcessRow` gained a `critical: bool`. Heuristic: `holds_vram OR (cpu_pct > 50 AND name not in _IDLE_NAMES) OR (mem / total > 0.10)`. `_IDLE_NAMES` filters out `System Idle Process`, `System`, `Idle`, `kernel_task`, `swapper`, `kworker`.
- **ProcessTable visual** — critical rows render with a `★` prefix and bold cyan via `rich.text.Text.stylize`. Non-critical rows get a two-space prefix to keep column alignment.

### v0.0.3 additions

- **GPU probe** — added clocks (`nvmlDeviceGetClockInfo` for graphics + memory), power draw + cap (`nvmlDeviceGetPowerUsage` / `nvmlDeviceGetPowerManagementLimit`), and active throttle reasons (`nvmlDeviceGetCurrentClocksThrottleReasons`). Each NVML call is individually `_try`-fenced so Optimus laptops and older drivers that omit a field still show the rest. Throttle line surfaces only when present, in yellow Rich markup.
- **CPU probe** — added best-effort temperature via `psutil.sensors_temperatures()`. Prefers `coretemp`/`k10temp`/`zenpower`/`cpu_thermal`/`acpitz` package sensors; falls back to first available; returns `n/a` on Windows (psutil has no Windows backend — WMI/OpenHardwareMonitor backend is roadmapped).
- **Kill action** — new `k` binding in `WattsonApp`. Reads the selected row from `ProcessTable.selected()` (the table now mirrors structured rows in `_rows_data` so the cursor maps back to a `ProcessRow`). Pushes a `ConfirmKill` `ModalScreen` (centred 60×11 panel, Cancel/Kill buttons + `y`/`n`/`Esc` bindings). On confirm, calls `processes.terminate(pid)` which is best-effort and returns `{ok, message}`; the app surfaces it via `notify()` toast (warning on success, error on failure).
- **CSS** — `#stats-grid` height bumped 20 → 23 to fit the GPU panel's 5–6 data lines (name + util/MemBW/temp + VRAM + clock/power + optional throttle).
- **Probe contract** — unchanged: stat probes still return strings, `processes.snapshot()` still returns `list[ProcessRow]`. The string probes now embed Rich markup (e.g. `[yellow]Throttle:[/]`) so the panel renders coloured fragments.

### v0.0.2 additions

- `src/wattson/probes/processes.py` — `ProcessProbe` class (stateful: keeps psutil.Process cache so `cpu_percent()` deltas are meaningful from snapshot #2 onwards). Cross-references NVML compute processes with psutil for per-process VRAM, CPU%, memory, cmdline.
- `ProcessTable` widget in `app.py` (extends `DataTable`); rows sorted GPU-first by VRAM desc, then non-GPU by CPU% desc.
- **Probe contract evolved as anticipated**: `processes.snapshot()` returns `list[ProcessRow]` (TypedDict), not a string. Other probes still return strings for now — only evolve when needed.

## Layout

```
wattson/
├── pyproject.toml          # hatchling, src layout, ruff config
├── README.md
├── LICENSE                 # MIT
├── src/wattson/
│   ├── __init__.py         # __version__
│   ├── __main__.py         # `python -m wattson` entry
│   ├── app.py              # textual App + StatPanel widget
│   └── probes/
│       ├── __init__.py     # re-exports cpu, gpu, memory, disk
│       ├── cpu.py
│       ├── gpu.py          # NVML; graceful no-GPU fallback
│       ├── memory.py
│       └── disk.py
├── tests/test_probes.py    # smoke tests
└── CLAUDE.md
```

## Probe contract

Each probe module exposes **`snapshot() -> str`**. The TUI panels call it once per refresh and render the returned string. Static info (CPU brand, GPU model) is `@lru_cache`d inside the probe.

This stays a string for v0 to keep the surface small. When probes need to carry state (history, deltas, throttling reasons) they'll become classes — at that point the contract becomes `.snapshot() -> RichRenderable` on a probe instance.

## Design decisions

| Decision | Why |
|---|---|
| Python (not Rust/Go) | User's daily stack is Python; pip-installable matters for DL devs; textual lets us iterate fast |
| `textual` TUI (not `rich.live`) | Real widget tree + key bindings + CSS-like styling; trivial to add more panels later |
| `nvidia-ml-py` (not parsing nvidia-smi) | Stable Python API, no subprocess parsing, NVIDIA-supported |
| `psutil` for CPU/mem/disk | Cross-platform; battle-tested |
| `src/` layout + `hatchling` | Modern Python packaging; PEP 517; avoids import-from-cwd footgun |
| Probes return strings (not structs) for v0 | Smaller surface; refactor when needed |
| Each probe wrapped in try/except in the panel | One bad probe must not crash the loop |
| Was private at start, public from v0.0.2 | Started private to avoid an empty-shell on the profile. Flipped public once the process table shipped + user wanted it pinned. |

## Planned features (from the original brief)

1. ✅ Task-manager-style perf (CPU/GPU/Mem/Disk) — v0.0.1
2. ✅ `nvidia-smi` output equivalents — process list (v0.0.2), clocks + power + throttle reasons (v0.0.3)
3. 🟡 Temperatures — GPU done (v0.0.1), CPU Linux/Mac done (v0.0.3); Windows CPU temp still needs WMI/OHM backend
4. ✅ Throttling alerts — `nvmlDeviceGetCurrentClocksThrottleReasons` (v0.0.3)
5. 🟡 Process actions — **kill done (v0.0.3)**; priority + CPU-affinity controls still TBD
6. ⏳ Boost / power-limit (`nvmlDeviceSetPowerManagementLimit`, needs admin)
7. 🟡 Richer interactive UI — hardware screen (v0.0.4) + sparkline trends screen (v0.0.5); per-GPU drill-in still TBD
8. ✅ Background-process ranking with criticality flagging (v0.0.4)
9. ✅ Full hardware inventory — driver, CUDA, UUID, serial, PCIe gen+width, CPU cache + flags (v0.0.4)
10. (TBD)

## Dev workflow

```powershell
cd C:\Users\anjan\Desktop\Goals\github\wattson
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
wattson                  # run the TUI
pytest                   # smoke tests
ruff check .             # lint
```

## Things NOT to do

- Don't add a web UI yet — v1 is TUI-only by decision. Revisit once core features stabilise.
- Don't merge probes into `app.py` — clean separation is the whole point of `probes/`.
- Don't make GPU probe crash when NVML isn't available — DL devs without GPUs (or on AMD/Mac) should still see the other panels.
- Don't introduce `subprocess` calls to parse `nvidia-smi` output — pynvml covers everything.
- ~~Don't flip the repo public until items 2, 3, 5 are working~~ — user opted to make it public at v0.0.2 once the process table shipped.
