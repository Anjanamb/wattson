# wattson

> Your machine's personal assistant — a DL-workload-aware system monitor.

A terminal UI for the bits of system monitoring that matter when you're running deep-learning workloads: GPU utilisation per training job, thermal headroom, throttling alerts, and the hardware details you forget every time someone asks *"wait, what model GPU is in this rig?"*

**Status:** `v0.0.2` — 4-stat dashboard + GPU-aware process table, 1 Hz refresh.

## Planned features

### Shipped
- [x] CPU usage, frequency, model, core count *(v0.0.1)*
- [x] NVIDIA GPU utilisation, VRAM, temperature with graceful no-GPU fallback *(v0.0.1)*
- [x] System memory + swap *(v0.0.1)*
- [x] Disk usage per partition *(v0.0.1)*
- [x] Live-refreshing TUI (textual) *(v0.0.1)*
- [x] **GPU-aware process table** — top processes by VRAM + CPU, with which-GPU attribution *(v0.0.2)*

### Coming
- [ ] Thermal history + throttling alerts (`nvidia-smi --query-gpu=clocks_throttle_reasons.*`)
- [ ] Process kill / priority controls (from inside the TUI)
- [ ] CPU/GPU boost / power-limit controls (`nvidia-smi -pl`, governor switching)
- [ ] Background-process ranking with "critical to your workload" flagging
- [ ] Full hardware inventory (PCIe lane width, NVLink topology, driver version, board serial)
- [ ] Watchdog mode — log alerts to disk when training jobs throttle, OOM, or crash
- [ ] Multi-host — watch a small training cluster from one terminal

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

Inside the TUI: `q` to quit, `r` to force-refresh.

## Why "wattson"?

Watt (power) + Watson (assistant). Your hardware burns watts; wattson watches.

## License

MIT — see [LICENSE](LICENSE).
