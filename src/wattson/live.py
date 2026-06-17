"""Rich Live dashboard — the new default `wattson` mode.

Built after v0.0.15 ran out of perf budget inside Textual on Windows.
This module is intentionally tiny: no event loop, no widget tree, no
CSS. Just a `rich.layout.Layout` re-rendered every second by
`rich.live.Live`. Ctrl+C to quit. Actions (kill, priority, power
limit, …) live in `cli.py` as one-shot subcommands so we don't
re-introduce interactive complexity here.

The earlier Textual app is still available via `wattson tui`.
"""

from __future__ import annotations

import time

from rich.console import Console, Group
from rich.layout import Layout
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .probes import cpu, disk, gpu, memory, processes


def _stat_panel(title: str, body: str, colour: str = "cyan") -> Panel:
    """Render one stat panel — the same content cpu/gpu/memory/disk
    snapshot() functions return, framed."""
    return Panel(
        Text.from_markup(body),
        title=f"[bold {colour}]{title}[/bold {colour}]",
        title_align="left",
        border_style=colour,
        padding=(0, 1),
    )


def _process_table(rows: list[dict]) -> Panel:
    """Top-N process table — same data as the TUI's DataTable, but a
    plain Rich Table."""
    table = Table(
        show_header=True,
        header_style="bold #9aa3ad",
        border_style="cyan",
        pad_edge=False,
        expand=True,
    )
    table.add_column("PID",     justify="right",  no_wrap=True)
    table.add_column("NAME",                    no_wrap=True)
    table.add_column("CPU%",    justify="right",  no_wrap=True)
    table.add_column("MEM MB",  justify="right",  no_wrap=True)
    table.add_column("GPU",     justify="center", no_wrap=True)
    table.add_column("VRAM MB", justify="right",  no_wrap=True)
    table.add_column("COMMAND",                 overflow="ellipsis", no_wrap=True)

    for r in rows:
        gi, vm = r.get("gpu_idx"), r.get("vram_mb")
        gpu_str = f"#{gi}" if gi is not None else "—"
        vram_str = f"{vm:.0f}" if vm is not None else "—"
        critical = r.get("critical", False)

        name_cell = ("[bold cyan]★ " if critical else "  ") + r["name"][:24]
        if critical:
            name_cell += "[/bold cyan]"

        table.add_row(
            str(r["pid"]),
            Text.from_markup(name_cell),
            f"{r['cpu_pct']:.1f}",
            f"{r['mem_mb']:.0f}",
            gpu_str,
            vram_str,
            r["cmdline"],
        )

    return Panel(
        table,
        title="[bold cyan]Top processes[/bold cyan]",
        title_align="left",
        border_style="cyan",
        padding=(0, 0),
    )


def _safe_snapshot(getter) -> str:
    """Probe wrapper that surfaces failures inline instead of crashing
    the whole dashboard."""
    try:
        return getter()
    except Exception as e:
        return f"[red]probe error:[/red] {type(e).__name__}: {e}"


def _safe_processes() -> list[dict]:
    try:
        return processes.snapshot(limit=15)
    except Exception:
        return []


def _render() -> Layout:
    """Build the full Layout for the current tick."""
    stats = Layout(name="stats", size=10)
    stats.split_row(
        Layout(_stat_panel("CPU",    _safe_snapshot(cpu.snapshot),    "cyan")),
        Layout(_stat_panel("GPU",    _safe_snapshot(gpu.snapshot),    "green")),
        Layout(_stat_panel("Memory", _safe_snapshot(memory.snapshot), "blue")),
        Layout(_stat_panel("Disk",   _safe_snapshot(disk.snapshot),   "magenta")),
    )

    proc_panel = _process_table(_safe_processes())

    root = Layout()
    root.split_column(
        stats,
        Layout(proc_panel, name="processes"),
    )
    return root


def run(interval: float = 1.0) -> None:
    """Live dashboard loop. Returns on Ctrl+C / KeyboardInterrupt.

    `interval` is the redraw cadence in seconds. 1.0 is the
    `gpustat -i 1` / `htop` default; smaller values give a snappier
    feel but cost more CPU.
    """
    console = Console()
    refresh_hz = max(1, int(round(1 / interval)))
    try:
        with Live(
            _render(),
            console=console,
            refresh_per_second=refresh_hz,
            screen=True,  # alternate screen buffer — restores terminal on exit
            transient=False,
        ) as live:
            while True:
                time.sleep(interval)
                live.update(_render())
    except KeyboardInterrupt:
        pass


def status() -> int:
    """One-shot snapshot — render once, exit. Used by `wattson status`."""
    Console().print(_render())
    return 0
