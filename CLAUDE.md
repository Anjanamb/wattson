# wattson — Claude project notes

DL-workload-aware system monitor. TUI built on `textual`. Started 2026-05-23.

Parent workspace: `Goals\github\CLAUDE.md` (gh CLI auth, conventions).

## Current state

`v0.0.17` — Live-dashboard polish on top of v0.0.16's strategy pivot. User reported three issues with the new Rich Live mode: process table columns squished to `...`, Disk panel showed `SystemError` again, CPU temp `n/a`. Fixed the first two (real bugs); the third is a hardware limitation explained in the README.

### v0.0.17 fixes

- **Process table columns.** v0.0.16 used `Table(expand=True)` with all columns set to `no_wrap=True` but no explicit widths. Rich proportionally shrank every column equally to fit, so PID / CPU% / MEM / GPU / VRAM collapsed to `...`. Fix: explicit `width=` on each numeric/short column (PID=7, NAME=28, CPU%=6, MEM=8, GPU=4, VRAM=8) and `ratio=1` on COMMAND so it gets the leftover. NAME also gets `overflow="ellipsis"`.
- **Windows disk panel.** psutil's `disk_usage` C extension raises `SystemError` on some Windows configurations (BitLocker mid-unlock, virtual drives from VPN clients, etc.). New `_windows_disk_usage(mount)` in `probes/disk.py` calls `kernel32!GetDiskFreeSpaceExW` directly via ctypes, returns the same `{total, used, free, percent}` interface as psutil's namedtuple, raises `OSError` on actual API failure. `_disk_usage(mount)` dispatches: Windows → ctypes path, everyone else → psutil. `psutil.disk_partitions` is still used to enumerate mounts — that part works reliably.
- **CPU temp on i7-13700H still `n/a`.** Not a code bug — the temperature lives in EC registers that modern Intel laptops only expose to vendor drivers. The ACPI WMI thermal zone (`MSAcpi_ThermalZoneTemperature`) returns nothing on these systems, and psutil has no Windows backend. Real fix is LibreHardwareMonitor running with its WMI provider enabled — `_temp_lhm()` already handles that path. Documented as a setup step rather than treated as a bug to chase.

### v0.0.16 additions

- **New module** `src/wattson/live.py` — Rich `Live` dashboard. Five layout cells: 4 stat panels in a row + 1 process table below. Each tick re-renders the whole Layout (`live.update(_render())`); no widget identity, so no state-management complexity. `screen=True` uses the alternate screen buffer so the terminal is restored on Ctrl+C. Probes wrapped in `_safe_snapshot()` so a single failure shows inline as a `[red]probe error:[/red]` instead of crashing the dashboard.
- **New module** `src/wattson/cli.py` — argparse dispatcher. Subcommands: `live`, `tui`, `status`, `kill`, `nice`, `power`. No subcommand = default `live` with 1 s interval. Actions are CLI commands now (`wattson kill 1234`), which removes the need to keep an interactive event loop alive just to handle keypresses.
- **Entry point change.** `pyproject.toml`'s `[project.scripts] wattson = "wattson.cli:main"` (was `wattson.__main__:main`). `__main__.py` updated to dispatch through CLI so `python -m wattson` works the same way.
- **Original Textual app preserved.** `src/wattson/app.py` unchanged; `wattson tui` still launches the full experience (watchdog screen, trends, drill-down, hardware inventory, affinity / priority / power modals, etc.). The pivot isn't a deletion — it's a default-mode swap.

### Why this should finally help

Rich `Live` writes ANSI directly to stdout and doesn't maintain a widget tree, a CSS engine, or an async event loop. The per-tick cost is approximately one `Layout` render + one terminal write. Compared to Textual's per-tick budget (interval callback → reactive watchers → CSS reflow → composite → diff → flush), it's a different order of magnitude. The downside is no keyboard interactivity inside the dashboard — the trade is explicit: Live for snappy passive monitoring, CLI subcommands for actions.

### v0.0.15 additions / fixes

- **Refresh cadence halved.** `_refresh_light` now runs every 2 s (was 1 s) and `_refresh_processes` every 3 s (was 2 s). On a system monitor, slightly staler data is a trade most users gladly take for an instant key response. The genuinely-fast probes (psutil cpu/mem/disk) don't lose anything visible at 2 s; the cached GPU NVML round happens once per 2-s tick now instead of twice.
- **`_refresh_light` is now async.** Same body, but a coroutine. Lets us `await asyncio.sleep(0)` between panel refreshes and between metric collection / watchdog check — that yield is what lets `t` / `g` / `k` / etc. cut the queue instead of waiting for the whole refresh to finish.
- **Reactive write guards.** `StatPanel.refresh_body()` now skips the assignment when the new body equals the old. Same guard on `self.sub_title`. Textual still schedules a re-render on every reactive set, even with identical values; this is a free win on idle desks where panel content doesn't actually move every tick.
- **`_refresh_all` shim simplified.** Was triggering the light path after kill/priority/affinity actions; now just nudges the process worker (the only thing visibly affected by those actions). Removes the awkwardness of calling an async coroutine from a sync callback.

### v0.0.14 additions / fixes

- **Reverted worker complexity.** `_refresh_panels` and `_refresh_metrics` from v0.0.13 are gone. Back to v0.0.12's `_refresh_light` for stat panels + metrics, called synchronously at 1 Hz. `_refresh_processes` keeps its worker (the win from v0.0.12). The v0.0.13 panel-truncation regression — where multi-line bodies set via `call_from_thread` only rendered the first line — is fixed by simply not going through that code path anymore.
- **Single NVML round per tick.** `gpu.py` now has `_collect()` / `_collect_uncached()`. The collector does *one* pass of NVML calls (name, util, mem, temp, clock, power, throttle reasons for every GPU) and returns a structured dict. `snapshot()`, `metrics()`, and `throttle_masks()` all format slices of that dict. Result is wrapped in a `_TTL_SEC = 0.5` cache, so a 1 Hz refresh that calls all three functions only triggers NVML once every other tick — total NVML traffic drops from ~14 calls/sec to ~7 every 2 seconds.
- **Cache TTL choice.** 0.5 s on GPU because util / clock / power genuinely change second-to-second; 10 s on disk because usage barely moves and the user's box pays SystemError-handling cost on every miss; 5 s on CPU temp because temperature doesn't move fast and WMI is expensive.

### v0.0.13 additions / fixes

- **Three worker groups, three intervals.** `WattsonApp.on_mount` now starts three workers and schedules each with `set_interval`:
  - `_refresh_panels` (`group="panels"`, 1 Hz) — calls `cpu.snapshot()`, `gpu.snapshot()`, `memory.snapshot()`, `disk.snapshot()` on the worker thread. Results returned as `{panel_id: text}` via `call_from_thread(self._apply_panels, snapshots)`. The sink sets the reactive `.body` on each `StatPanel`, which schedules its re-render in Textual's normal pipeline.
  - `_refresh_metrics` (`group="metrics"`, 1 Hz) — calls `cpu.metrics()`, `memory.metrics()`, `gpu.metrics()`, `gpu.throttle_masks()` on the worker. Returns `(all_metrics, masks)` to `_apply_metrics`, which feeds HISTORY + WATCHDOG + sub-title from the main thread.
  - `_refresh_processes` (`group="processes"`, 2 Hz) — unchanged from v0.0.12.
- All three use `exclusive=True` so a stalled probe can't pile up calls. `group=...` keeps them in separate worker pools.
- **Main thread is now ~free.** The only main-thread work per tick is the three interval callbacks (each of which just schedules a worker), Textual's compositor, and key event dispatch. Pressing `t` / `g` / `k` / etc. should feel instant.
- **`_refresh_all` shim** updated to fire `_refresh_panels()` + `_refresh_metrics()`. The kill / priority / affinity / power-limit callbacks still call it; the process snapshot keeps its 2 s cadence.
- **Disk caching.** `disk.snapshot()` wraps `_snapshot_uncached()` in a 10 s cache (`_DISK_TTL_SEC`). User's box raises `SystemError` per partition every call (~10-50 ms of exception-handling cost each); paying that once every 10 s instead of every second is ~90 % cheaper for that probe.

### v0.0.12 additions / fixes

- **Worker-thread process snapshot.** `WattsonApp` gains `_refresh_light` (main thread, 1 Hz) and `_refresh_processes` (`@work(exclusive=True, thread=True)`, 2 Hz). The light path does stat panels + history + watchdog as before. The worker does `processes.snapshot(limit=20)` off-thread, then dispatches the result list to `_apply_process_rows` via `call_from_thread` so the DataTable update happens on the main thread (Textual requirement). `exclusive=True` ensures at most one snapshot in flight even if a tick gets delayed.
- **Backwards-compat shim.** Existing controlling actions (kill, priority, affinity, power-limit callbacks) call `self._refresh_all()` to nudge a UI refresh after the action. The method is now a one-liner that calls `_refresh_light()`; the process snapshot catches up on its own 2 s cadence. Avoids touching every callsite while keeping the public method working.
- **Refresh cadence rationale.** Process snapshot at 2 Hz instead of 1 Hz: on Windows the snapshot itself is ~150-300 ms even after the v0.0.11 two-phase pass. At 1 Hz we'd be paying that 30 % of every tick; at 2 Hz it's 15 %. Stat panels and metrics stay 1 Hz because they're cheap (psutil scalars + NVML, ~5-15 ms total).
- **Disk diagnostics.** `disk.snapshot()` now tries `disk_partitions(all=False)` first, falls back to `all=True` if the strict call returns nothing (some Windows configs only expose removable volumes in the strict list). When `disk_usage` fails on every partition, the panel says `all partitions failed — e.g. C:\\: OSError` instead of pretending no partitions exist. Helps figure out *which* exception class is actually being raised on the user's box.

### v0.0.11 additions / fixes

- **Disk panel stability.** `disk.snapshot()` used to catch only `PermissionError` / `OSError`. On Windows some volumes (removable, locked, network shares mid-reconnect) make psutil's C extension raise `struct.error` or `ValueError` instead, which then bubbled all the way up and showed as a probe-error message in the panel. Now the inner loop catches a bare `Exception` per partition, and `disk_partitions(all=False)` itself is wrapped so even that can degrade gracefully.
- **CPU temp caching.** `_temp()` was re-running both WMI ACPI **and** the LHM/OHM enumerate every single refresh tick (1 Hz). On Windows that's ~300-800 ms of WMI per tick — the user-visible slowdown. New: module-level `_temp_cache` with `_TEMP_TTL_SEC = 5` so the actual probe runs once every 5 s; intermediate ticks just hand back the cached value. CPU temperature doesn't move fast enough for sub-second resolution to matter.
- **WMI connection caching.** `wmi.WMI(namespace=...)` setup is ~50-200 ms per namespace. v0.0.10 created a fresh connection every call. New `_get_wmi(namespace)` lazily constructs each connection once and stores it in `_wmi_conn_cache` for the lifetime of the process. Both `_temp_wmi` and `_temp_lhm` route through it.
- **Two-phase process snapshot.** v0.0.10's `processes.snapshot()` called `proc.cmdline()` for every process (~200-300 on a typical Windows box). On Windows that syscall can be ~10-30 ms per call. The new structure:
  1. Phase 1: light pass — only `name`, `cpu_percent`, `memory_info` (cheap, batched in `oneshot()`) for every PID.
  2. Sort by the same composite key (`vram → cpu_pct → mem_mb`).
  3. Phase 2: `cmdline()` for the top `limit` rows only.
  Net effect: `cmdline()` runs on ~20 processes instead of ~300, dropping per-tick cost by roughly 50×.

### v0.0.10 additions

- **probes/cpu.py — LHM/OHM fallback.** `_temp_lhm()` enumerates Sensor records in `root\\LibreHardwareMonitor` (and `root\\OpenHardwareMonitor` for legacy installs), filters to `SensorType == "Temperature"` with "CPU" in the name, and returns the **hottest** reading — that's the package, not an idle core. `_temp()` chain is now psutil → ACPI WMI → LHM/OHM → `n/a`. Requires LHM running with the WMI provider enabled.
- **probes/processes.py — CPU affinity.**
  - `cpu_affinity_info(pid)` returns `{current: [int], total: int}` or `None` (macOS / gone / denied). `total = psutil.cpu_count(logical=True)`.
  - `set_affinity(pid, cores)` wraps `psutil.Process.cpu_affinity(cores)`. macOS NotImplementedError, AccessDenied, NoSuchProcess, ZombieProcess, and ValueError are all caught and surfaced as friendly `{ok: False, message: ...}` strings.
- **probes/gpu.py — `device_info(idx)`** for the drill-down: `{name, uuid, serial, pcie}`. Each NVML call individually try-fenced; serial is usually `n/a` without admin.
- **app.py — `SetAffinity` ModalScreen.** Centred 72×15 grid with: question label, current/total info line, `Input` for the core list, and Cancel / All cores / Apply buttons. Input parser accepts `0,1,2,3` and `0-7,16-19` style. Client-side validation: integer parse, non-empty, in `[0, total)`.
- **app.py — `GPUDrillScreen`.** Pushed via the new `g` keybinding. Scrollable layout: Hardware (`device_info`, cached after first paint), Current (live util / MemBW / temp / VRAM% / power + throttle reasons), 4 line charts (util, temp, power, VRAM%), and a DataTable filtered to processes whose `gpu_idx == self.gpu_idx`. Sub-title is `GPU{idx} drill-down`. Always opens GPU 0 for now — the multi-GPU picker stays on the roadmap.
- **WattsonApp.** Two new bindings (`a`, `g`) and matching action handlers + callback factories that follow the established `notify(...)` pattern.

### v0.0.9 additions

- **New dep** `textual-plotext>=0.2` (which pulls in `plotext`). Wraps plotext as a Textual widget; we use `marker="braille"` for high-resolution lines.
- **New dep** `wmi>=1.5; sys_platform == 'win32'` — platform-conditional, only installed on Windows.
- **probes/cpu.py — Windows temperature fallback** — `_temp()` now tries `_temp_psutil()` first, then `_temp_wmi()` on `sys.platform == "win32"`. `_temp_wmi()` queries `MSAcpi_ThermalZoneTemperature` (CurrentTemperature is in tenths of Kelvin → °C); sanity-checks the value (-50 to 200 °C) and returns `None` if anything's off. Many modern laptops don't expose CPU temp in ACPI — the next step there is a LibreHardwareMonitor backend, noted in README "Coming".
- **TrendsScreen rewrite** — `Sparkline` widgets replaced with `PlotextPlot`. Per-tick refresh: `plot.plt.clear_figure()` → `plot.plt.plot(xs, data, marker="braille", color=…)` → `plt.theme("dark")` → `plot.refresh()`. plotext takes named colours directly (`"cyan"`, `"red"`, `"blue"`, `"green"`, `"magenta"`) so the CSS pseudo-element gymnastics from v0.0.8 are gone. PlotextPlot height = 7; with 6 series for a single-GPU rig that's ~48 rows, scrollable.
- **CSS gotcha cleared up:** with `Sparkline` you had to nest `> .sparkline--max-color { color: ...; }` inside a class selector to colour each bar. `PlotextPlot` accepts the colour through plt API — no descendant selector needed.

### v0.0.8 additions / fixes

- **Fix:** `ConfirmKill.__init__` and `SetPriority.__init__` were assigning `self.name = name`. `Widget.name` is a CSS-queryable read-only property in modern Textual; the assignment raised on every press. Renamed to `self.proc_name` and updated both `compose()` f-strings.
- **Reserved-name comment** added to both modals — `name`, `id`, `classes`, `styles` are framework properties on Widget; future modals must namespace their own attributes.
- **TrendsScreen rework:**
  - Each row is now a `Static` label + `Sparkline`. The label is updated every tick from history with `now <value> · min <…> · max <…> · <N>/60 s` so the relative-scaled sparkline becomes interpretable in absolute terms.
  - Five colour classes wired through CSS: `cpu-color`, `gpu-color`, `mem-color`, `temp-color`, `power-color`. Each sets distinct `.sparkline--max-color` and `.sparkline--min-color` values so the same metric type uses a consistent colour across rows.
  - Sparkline height bumped 3 → 4 rows for more vertical resolution.
- **CSS gotcha noted in code:** Textual's `Sparkline` looks up `.sparkline--max-color` / `.sparkline--min-color` as *descendant pseudo-elements*. The selector pattern is `Sparkline.your-class > .sparkline--max-color { color: ...; }`.

### v0.0.7 additions

- **probes/processes.py — `set_priority(pid, level)`** where `level ∈ {'low','normal','high'}`. Maps to `psutil.{BELOW,_NORMAL,ABOVE_NORMAL}_PRIORITY_CLASS` on Windows and to nice values +10/0/-5 on Unix. Returns the same `{ok, message}` shape as `terminate`. `high` typically requires admin; psutil's `AccessDenied` is caught and surfaced as a clear toast string.
- **probes/gpu.py — `power_limit_info(idx)` and `set_power_limit(idx, watts)`** wrapping `nvmlDeviceGetPowerManagementLimit{,Constraints}`, `nvmlDeviceGetPowerUsage`, and `nvmlDeviceSetPowerManagementLimit`. `power_limit_info` returns `{name, current_w, cap_w, min_w, max_w}` or `None` when NVML / the constraints API isn't available. The setter returns `{ok, message}` — never raises.
- **app.py — `SetPriority` ModalScreen** with 3 buttons + `l`/`n`/`h` keyboard shortcuts; dismisses with the level string or `None`. **`SetPowerLimit` ModalScreen** with current/cap/range header, an `Input` field, and Cancel/Apply buttons; client-side validation (integer, in range) before `dismiss(value)`.
- **WattsonApp — `n` and `p` bindings** plus `action_priority` / `action_power_limit` and the matching callback factories that surface `set_priority` / `set_power_limit` results via `notify(...)`.
- **Multi-GPU note** — `p` currently always targets GPU0. The per-GPU drill-in screen will be the natural home for a GPU picker; until then, multi-GPU rigs can still set power limits via the `wattson` Python API (`from wattson.probes import gpu; gpu.set_power_limit(idx, watts)`).

### v0.0.6 additions

- **New module** `src/wattson/watchdog.py` — `Watchdog` class with `check(metrics, throttle_masks)` → returns event-count, internal per-category rate limiter (`RATE_LIMIT_SEC = 60`), JSONL writer at `~/.wattson/events.jsonl`. Thresholds are module-level constants (`CPU_TEMP_WARN`, `GPU_TEMP_WARN`, `MEM_PCT_WARN`, etc.) — tunable but not yet exposed via config. Module singleton `WATCHDOG`.
- **New probe outputs** — `gpu.metrics()` now also emits `gpu{i}.vram_pct`; added `gpu.throttle_masks() -> {gpu_idx: mask}` and a public `gpu.throttle_text(mask) -> str` alias (so the watchdog can resolve reasons without importing the internal `_throttle_text`).
- **New screen** `WatchdogScreen` in `app.py` — pushed via the new `w` keybinding. Tails the JSONL log (most-recent-first), refreshes every 2 s. Empty-state shows current thresholds and the log path.
- **Header counter** — `WattsonApp._refresh_all` composes all probe metrics, runs `WATCHDOG.check(...)`, and rewrites `sub_title` to include `⚠ {session_count}` once any event has been logged this session.
- **Tests** — `tests/test_watchdog.py` exercises threshold logic, severity escalation, rate limiting, throttle-mask events, and the recent-events round-trip. Uses per-test `tmp_path` fixture so tests don't touch the real log file.

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
5. 🟡 Process actions — kill (v0.0.3), priority (v0.0.7); CPU-affinity still TBD
6. ✅ Power-limit control via `nvmlDeviceSetPowerManagementLimit` — needs admin to apply (v0.0.7)
7. 🟡 Richer interactive UI — hardware screen (v0.0.4) + sparkline trends screen (v0.0.5) + watchdog screen (v0.0.6); per-GPU drill-in still TBD
8. ✅ Background-process ranking with criticality flagging (v0.0.4)
9. ✅ Full hardware inventory — driver, CUDA, UUID, serial, PCIe gen+width, CPU cache + flags (v0.0.4)
10. ✅ Watchdog mode — JSONL events on hot temps / memory / VRAM / throttle, with rate limiting and a tailing screen (v0.0.6)

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
