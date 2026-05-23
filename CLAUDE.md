# wattson — Claude project notes

DL-workload-aware system monitor. TUI built on `textual`. Started 2026-05-23.

Parent workspace: `Goals\github\CLAUDE.md` (gh CLI auth, conventions).

## Current state

`v0.0.1` — minimal scaffold. 4-panel TUI (CPU / GPU / Memory / Disk) refreshing at 1 Hz. Repo is **private** (`Anjanamb/wattson`); flip to public when there's a real MVP.

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
| Private repo at start | No empty-shell on profile; flip via `gh repo edit Anjanamb/wattson --visibility public` |

## Planned features (from the original brief)

1. ✅ Task-manager-style perf (CPU/GPU/Mem/Disk) — v0.0.1
2. ⏳ `nvidia-smi` output equivalents (process list, clocks, power, throttle reasons)
3. ⏳ Temperatures (CPU temps need `psutil.sensors_temperatures()` on Linux + WMI on Windows)
4. ⏳ Throttling — `nvmlDeviceGetCurrentClocksThrottleReasons`
5. ⏳ Kill processes (textual modal + `psutil.Process(pid).terminate()`)
6. ⏳ Boost / power-limit (`nvmlDeviceSetPowerManagementLimit`, needs admin)
7. ⏳ Richer interactive UI (per-GPU drill-in screens, sparklines)
8. ⏳ Background-process ranking with criticality flagging
9. ⏳ Full hardware inventory (PCIe lanes, NVLink, board S/N — `nvmlDeviceGetPciInfo`, `nvmlDeviceGetSerial`)
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
- Don't flip the repo public until at least items 2, 3, 5 are working.
