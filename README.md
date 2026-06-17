# wattson

> Your machine's personal assistant — a DL-workload-aware system monitor.

[![Status](https://img.shields.io/badge/status-active%20development-yellow?style=flat-square)](#planned-features)
[![Version](https://img.shields.io/badge/version-0.0.13-7DD3FC?style=flat-square)](#planned-features)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Textual](https://img.shields.io/badge/TUI-Textual-1E1E2E?style=flat-square)](https://textual.textualize.io/)
[![NVIDIA](https://img.shields.io/badge/GPU-NVIDIA_NVML-76B900?style=flat-square&logo=nvidia&logoColor=white)](https://developer.nvidia.com/nvidia-management-library-nvml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

A terminal UI for the bits of system monitoring that matter when you're running deep-learning workloads: GPU utilisation per training job, thermal headroom, throttling alerts, and the hardware details you forget every time someone asks *"wait, what model GPU is in this rig?"*

**Status:** `v0.0.13` — third perf pass. Every heavy probe (stat panels, metrics, processes) now runs in its own `@work(thread=True)` worker. The main thread does **nothing** but interval ticks and key events. Disk snapshot string is also cached for 10 s, since the user's box raised `SystemError` from psutil's C extension and re-raising at 1 Hz was visibly slow.

## Planned features

### Shipped

- [x] CPU usage, frequency, model, core count *(v0.0.1)*
- [x] NVIDIA GPU utilisation, VRAM, temperature with graceful no-GPU fallback *(v0.0.1)*
- [x] System memory + swap *(v0.0.1)*
- [x] Disk usage per partition *(v0.0.1)*
- [x] Live-refreshing TUI (textual) *(v0.0.1)*
- [x] **GPU-aware process table** — top processes by VRAM + CPU, with which-GPU attribution *(v0.0.2)*
- [x] **GPU clocks + power draw** — graphics/memory clock MHz, current/cap watts *(v0.0.3)*
- [x] **GPU throttle alerts** — surfaces active reasons (PowerCap, Thermal, HW slowdown, …) in yellow when present *(v0.0.3)*
- [x] **CPU temperature** — Linux/macOS via psutil sensors; Windows shows `n/a` until WMI/OHM backend lands *(v0.0.3)*
- [x] **Kill selected process** — `k` on a row → confirmation modal → SIGTERM via psutil, with status notification *(v0.0.3)*
- [x] **Hardware-inventory screen** — `i` opens a full-screen view of driver/CUDA versions, GPU UUID / serial / PCIe gen+width, CPU cache + AVX flags, OS / kernel / Python *(v0.0.4)*
- [x] **Process criticality flagging** — `★` marker + bold cyan styling on processes that hold VRAM (likely training jobs), sustain CPU > 50 %, or own > 10 % of system memory *(v0.0.4)*
- [x] **Trends screen** — `t` opens live sparklines for CPU %, memory %, and per-GPU util / temp / power over the last 60 s. Backed by a ring buffer in `wattson.history` *(v0.0.5)*
- [x] **Watchdog mode** — each tick, threshold checks for hot CPU/GPU, memory pressure, VRAM pressure, and active GPU throttle reasons. Events go to `~/.wattson/events.jsonl` (JSONL, rate-limited 1 / category / 60 s); session count surfaces in the header sub-title; `w` opens the tailing screen *(v0.0.6)*
- [x] **Process priority control** — `n` on the selected row opens Low / Normal / High buttons (or `l` / `n` / `h` shortcuts); psutil maps to nice values on Unix and PRIORITY_CLASS on Windows. High typically needs admin and surfaces an `Access denied` toast *(v0.0.7)*
- [x] **GPU power-limit control** — `p` opens a modal showing the current draw / cap / driver-reported min–max range and accepts a target wattage. Apply requires admin; failure path surfaces a clear error toast *(v0.0.7)*
- [x] **Line-chart Trends** — `t` now renders real braille line charts via `textual-plotext` / `plotext` rather than sparkline bars. Per-metric colours (CPU cyan · GPU green · memory blue · temps red · power magenta) with current / min / max in each row's label *(v0.0.9)*
- [x] **Windows CPU temperature** — falls back to WMI `MSAcpi_ThermalZoneTemperature` when psutil has no Windows backend, then to LibreHardwareMonitor / OpenHardwareMonitor for modern laptops that hide CPU temp behind the EC *(v0.0.9 + v0.0.10)*
- [x] **CPU affinity controls** — `a` on the selected row opens a modal that accepts a flexible core list (`0,1,2,3` or `0-7,16-19`); `All cores` button restores everything. Cross-platform via `psutil.Process.cpu_affinity()`; macOS surfaces a friendly "not supported" message instead of a crash *(v0.0.10)*
- [x] **Per-GPU drill-down screen** — `g` opens a dedicated view with the GPU's hardware info (name, UUID, serial, PCIe gen+width), live current values + throttle reasons, four braille line charts (util / temp / power / VRAM %), and a filtered table of processes holding VRAM on that GPU. Single-GPU rigs always drill into GPU 0; multi-GPU picker is the only thing still TBD here *(v0.0.10)*

### Coming

- [ ] Multi-host — watch a small training cluster from one terminal (separate distributed-systems project; not "polish")
- [ ] Multi-GPU picker modal (currently `g` always opens GPU 0)

## Install (dev)

```bash
git clone git@github.com:Anjanamb/wattson.git
cd wattson
python -m venv .venv
# Windows:  .venv\Scripts\Activate.ps1
# Unix:     source .venv/bin/activate
pip install -e ".[dev]"
wattson         # or: python -m wattson
```

Inside the TUI:

- `q` — quit
- `r` — force-refresh
- `↑` / `↓` — move the row cursor in the process table
- `k` — kill the selected process (with a confirmation modal — `y` / `n` / `Esc`)
- `n` — change priority of the selected process (`l` / `n` / `h` for Low / Normal / High; `Esc` to cancel)
- `a` — set CPU affinity of the selected process (comma/dash list or `All cores`)
- `p` — set GPU0 power limit (input is in watts; needs admin to apply)
- `g` — open the per-GPU drill-down (`g` / `q` / `Esc` to return)
- `i` — open the hardware-inventory screen (`i` / `q` / `Esc` to return)
- `t` — open the live trends screen (`t` / `q` / `Esc` to return)
- `w` — open the watchdog event log (`w` / `q` / `Esc` to return)

## Why "wattson"?

Watt (power) + Watson (assistant). Your hardware burns watts; wattson watches.

## License

[MIT](LICENSE) — see [anjanamb.github.io](https://anjanamb.github.io/) for more projects.
