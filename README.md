# wattson

> Your machine's personal assistant — a DL-workload-aware system monitor.

[![Status](https://img.shields.io/badge/status-active%20development-yellow?style=flat-square)](#planned-features)
[![Version](https://img.shields.io/badge/version-0.0.18-7DD3FC?style=flat-square)](#planned-features)
[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/)
[![Textual](https://img.shields.io/badge/TUI-Textual-1E1E2E?style=flat-square)](https://textual.textualize.io/)
[![NVIDIA](https://img.shields.io/badge/GPU-NVIDIA_NVML-76B900?style=flat-square&logo=nvidia&logoColor=white)](https://developer.nvidia.com/nvidia-management-library-nvml)
[![License: MIT](https://img.shields.io/badge/License-MIT-green?style=flat-square)](LICENSE)

A terminal UI for the bits of system monitoring that matter when you're running deep-learning workloads: GPU utilisation per training job, thermal headroom, throttling alerts, and the hardware details you forget every time someone asks *"wait, what model GPU is in this rig?"*

**Status:** `v0.0.18` — added WSL2 install path. The native-Windows code path is solid now, but for Windows users running DL workloads inside WSL Ubuntu, **wattson runs significantly snappier inside WSL** (Linux `psutil`, native `/proc` and `/sys` reads, no WMI dance for CPU temp, NVML works via NVIDIA's WSL2 CUDA driver). README has step-by-step setup. v0.0.17 fixes still apply: process table column widths are explicit, Windows disk reads use ctypes directly, CPU temp on the i7-13700H needs LibreHardwareMonitor on bare metal but reads cleanly in WSL.

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

### Running on WSL2 (recommended if you train inside WSL)

Windows users who run DL workloads in WSL Ubuntu will get a **noticeably snappier** experience than native Windows, because:

- Linux `psutil` is fast (no `cmdline()` slowdown, no `SystemError` on disk reads)
- Rich's ANSI rendering through the WSL terminal is lighter than Windows Terminal driving Textual/Rich for the native Python
- CPU temperature reads work via `/sys/class/thermal/thermal_zone*/temp` (no WMI / LHM dance)
- NVIDIA's WSL2 CUDA driver makes NVML work natively — GPU monitoring is identical to bare-metal Linux

**Setup** (inside your WSL Ubuntu shell):

```bash
sudo apt update
sudo apt install -y python3 python3-pip python3-venv git

git clone https://github.com/Anjanamb/wattson.git
cd wattson
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
wattson
```

**Important caveat:** wattson in WSL only sees the **WSL Linux environment** — your training processes, the host GPU (via the WSL2 NVIDIA driver), host CPU stats, WSL's memory budget. It does **not** see Windows processes (`explorer.exe`, etc.) or Windows-native disk usage. If you want the full-Windows view, run wattson on the Windows side. If you want to monitor your DL workloads, WSL is the better experience.

**GPU prerequisite:** NVIDIA Windows driver ≥ 470, WSL2 (not WSL1), and the NVIDIA Container Toolkit isn't required — just `pip install nvidia-ml-py` (which wattson does already) and the driver does the rest.

CLI reference:

```bash
wattson                                # default Live dashboard, Ctrl+C to quit
wattson live -i 2                      # custom refresh interval
wattson tui                            # original Textual app (all 6 screens)
wattson status                         # one-shot snapshot, print and exit
wattson kill <pid>                     # SIGTERM
wattson nice <pid> low|normal|high     # set priority
wattson power <watts> [--gpu N]        # set GPU power limit
```

Inside the `tui` mode:

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
