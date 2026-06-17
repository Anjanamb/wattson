"""wattson TUI — 4-stat dashboard, GPU-aware process table with criticality
markers, kill action, hardware-inventory screen (`i`), live trends with
sparklines (`t`), and a watchdog that logs throttle / OOM / hot-temp
events to disk (`w` opens the log)."""

from __future__ import annotations

from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, ScrollableContainer, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen, Screen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    Static,
)
from textual_plotext import PlotextPlot

from .history import HISTORY
from .probes import cpu, disk, gpu, hardware, memory, processes
from .watchdog import WATCHDOG


class StatPanel(Static):
    """One resource panel; re-renders when `body` changes.

    `refresh_body()` skips the reactive assignment when the new value
    is the same as the old one — Textual will still schedule a
    re-render on every set, so guarding here saves real work when
    nothing's moved (idle desk, snapshot returned an identical string).
    """

    body: reactive[str] = reactive("loading…")

    def __init__(self, title: str, body_getter, **kwargs) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._getter = body_getter

    def render(self) -> str:
        return f"[bold cyan]{self._title}[/bold cyan]\n\n{self.body}"

    def refresh_body(self) -> None:
        try:
            new_body = self._getter()
        except Exception as e:  # never let one bad probe kill the loop
            new_body = f"[red]probe error:[/red] {e}"
        if new_body != self.body:
            self.body = new_body


class ProcessTable(DataTable):
    """Top processes by GPU + CPU usage. Refreshed by the parent App.

    Maintains a parallel `_rows_data` list so the App can map the cursor
    row back to a structured ProcessRow for actions like kill.

    Rows marked `critical` (holds VRAM, sustained high CPU, or memory hog)
    get a leading ★ and are styled bold cyan so training/inference jobs
    jump off the screen at a glance.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows_data: list[dict] = []

    def on_mount(self) -> None:
        self.cursor_type = "row"
        self.zebra_stripes = True
        self.add_columns(
            "PID", "NAME", "CPU%", "MEM MB", "GPU", "VRAM MB", "COMMAND"
        )

    def refresh_rows(self, rows: list[dict]) -> None:
        # preserve cursor across refreshes
        cursor_row = self.cursor_row
        self.clear()
        self._rows_data = rows
        for r in rows:
            try:
                gi, vm = r["gpu_idx"], r["vram_mb"]
                gpu_str = f"#{gi}" if gi is not None else "—"
                vram_str = f"{vm:.0f}" if vm is not None else "—"
                critical = r.get("critical", False)
                marker = "★ " if critical else "  "
                name = Text(marker + r["name"][:22])
                if critical:
                    name.stylize("bold cyan")
                # Wrap every cell in Text(...) so DataTable does not try
                # to parse stray brackets / Rich-markup characters in
                # cmdlines or process names — Windows in particular has
                # plenty of those, and a single bad row would otherwise
                # blank the whole table.
                self.add_row(
                    Text(str(r["pid"])),
                    name,
                    Text(f"{r['cpu_pct']:.1f}"),
                    Text(f"{r['mem_mb']:.0f}"),
                    Text(gpu_str),
                    Text(vram_str),
                    Text(r["cmdline"]),
                )
            except Exception:
                # Skip the bad row — never let one row kill the whole table.
                continue
        if 0 <= cursor_row < self.row_count:
            self.move_cursor(row=cursor_row)

    def selected(self) -> dict | None:
        if 0 <= self.cursor_row < len(self._rows_data):
            return self._rows_data[self.cursor_row]
        return None


class ConfirmKill(ModalScreen[bool]):
    """Two-button confirmation modal for terminating a process."""

    DEFAULT_CSS = """
    ConfirmKill {
        align: center middle;
    }
    ConfirmKill > Grid {
        grid-size: 2;
        grid-columns: 1fr 1fr;
        grid-rows: 3 3;
        grid-gutter: 1 2;
        padding: 1 2;
        width: 60;
        height: 11;
        border: thick #38bdf8;
        background: #14171c;
    }
    ConfirmKill Label#question {
        column-span: 2;
        content-align: center middle;
        height: 3;
        color: #e6e8eb;
    }
    ConfirmKill Button {
        width: 100%;
    }
    """

    BINDINGS = [
        ("escape", "dismiss(False)", "Cancel"),
        ("y", "dismiss(True)", "Confirm"),
        ("n", "dismiss(False)", "Cancel"),
    ]

    def __init__(self, pid: int, name: str) -> None:
        # NB: don't store on `self.name` — Textual's Widget exposes a
        # read-only `name` property for CSS queries; assigning to it
        # raises `AttributeError: ... no setter`. Same goes for `id`,
        # `classes`, `styles`, etc. Use namespaced attrs instead.
        super().__init__()
        self.pid = pid
        self.proc_name = name

    def compose(self) -> ComposeResult:
        with Grid():
            yield Label(
                f"Kill PID {self.pid}  ({self.proc_name})?",
                id="question",
            )
            yield Button("Cancel", variant="primary", id="cancel")
            yield Button("Kill", variant="error", id="confirm")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm")


class SetPriority(ModalScreen[str | None]):
    """Pick one of low / normal / high. Dismisses with the chosen level
    or `None` for cancel."""

    DEFAULT_CSS = """
    SetPriority {
        align: center middle;
    }
    SetPriority > Grid {
        grid-size: 4;
        grid-columns: 1fr 1fr 1fr 1fr;
        grid-rows: 3 3;
        grid-gutter: 1 1;
        padding: 1 2;
        width: 70;
        height: 11;
        border: thick #38bdf8;
        background: #14171c;
    }
    SetPriority Label#question {
        column-span: 4;
        content-align: center middle;
        height: 3;
        color: #e6e8eb;
    }
    SetPriority Button { width: 100%; }
    """

    BINDINGS = [
        ("escape", "dismiss(None)", "Cancel"),
        ("l", "dismiss('low')", "Low"),
        ("n", "dismiss('normal')", "Normal"),
        ("h", "dismiss('high')", "High"),
    ]

    def __init__(self, pid: int, name: str) -> None:
        # See ConfirmKill: `name` is reserved on Widget — use proc_name.
        super().__init__()
        self.pid = pid
        self.proc_name = name

    def compose(self) -> ComposeResult:
        with Grid():
            yield Label(
                f"Set priority — PID {self.pid}  ({self.proc_name})",
                id="question",
            )
            yield Button("Cancel", variant="default", id="cancel")
            yield Button("Low",    variant="primary", id="low")
            yield Button("Normal", variant="primary", id="normal")
            yield Button("High",   variant="warning", id="high")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
        else:
            self.dismiss(event.button.id)


class SetPowerLimit(ModalScreen[int | None]):
    """Read a target wattage (clamped to driver-reported min/max).

    Dismisses with the new wattage or `None` for cancel.
    """

    DEFAULT_CSS = """
    SetPowerLimit {
        align: center middle;
    }
    SetPowerLimit > Grid {
        grid-size: 2;
        grid-rows: 3 1 3 3;
        grid-gutter: 1 2;
        padding: 1 2;
        width: 64;
        height: 15;
        border: thick #38bdf8;
        background: #14171c;
    }
    SetPowerLimit Label.lbl {
        column-span: 2;
        content-align: center middle;
        color: #e6e8eb;
    }
    SetPowerLimit Input {
        column-span: 2;
    }
    SetPowerLimit Button { width: 100%; }
    """

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(self, gpu_idx: int, info: dict) -> None:
        super().__init__()
        self.gpu_idx = gpu_idx
        self.gpu_name = info["name"]
        self.current_w = info["current_w"]
        self.cap_w = info["cap_w"]
        self.min_w = info["min_w"]
        self.max_w = info["max_w"]

    def compose(self) -> ComposeResult:
        with Grid():
            yield Label(
                f"Power limit — GPU{self.gpu_idx}  ({self.gpu_name})",
                classes="lbl",
            )
            yield Label(
                f"Now: {self.current_w:.0f} W  ·  Cap: {self.cap_w:.0f} W "
                f" ·  Range: {self.min_w} – {self.max_w} W",
                classes="lbl",
            )
            yield Input(
                placeholder=f"new limit in W ({self.min_w}-{self.max_w})",
                id="wattage",
            )
            yield Button("Cancel", id="cancel")
            yield Button("Apply",  variant="warning", id="apply")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        # Apply path
        try:
            value = int(self.query_one("#wattage", Input).value.strip())
        except (ValueError, AttributeError):
            self.app.notify(
                "Enter a whole number.", severity="error", timeout=2,
            )
            return
        if not (self.min_w <= value <= self.max_w):
            self.app.notify(
                f"Must be {self.min_w}-{self.max_w} W.",
                severity="error", timeout=2,
            )
            return
        self.dismiss(value)


class SetAffinity(ModalScreen[list[int] | None]):
    """Pick a subset of CPU cores for the selected process.

    Accepts a comma- and dash-separated list (e.g. `0,1,2,3` or `0-7,16-19`).
    Dismisses with the parsed list or `None` for cancel. macOS doesn't
    expose affinity at all; the parent screen never opens this modal in
    that case.
    """

    DEFAULT_CSS = """
    SetAffinity {
        align: center middle;
    }
    SetAffinity > Grid {
        grid-size: 3;
        grid-rows: 3 1 3 3;
        grid-gutter: 1 2;
        padding: 1 2;
        width: 72;
        height: 15;
        border: thick #38bdf8;
        background: #14171c;
    }
    SetAffinity Label.lbl {
        column-span: 3;
        content-align: center middle;
        color: #e6e8eb;
    }
    SetAffinity Input {
        column-span: 3;
    }
    SetAffinity Button { width: 100%; }
    """

    BINDINGS = [("escape", "dismiss(None)", "Cancel")]

    def __init__(
        self,
        pid: int,
        name: str,
        current: list[int],
        total: int,
    ) -> None:
        # See ConfirmKill: `name` is reserved on Widget.
        super().__init__()
        self.pid = pid
        self.proc_name = name
        self.current = current
        self.total = total

    def compose(self) -> ComposeResult:
        with Grid():
            yield Label(
                f"CPU affinity — PID {self.pid}  ({self.proc_name})",
                classes="lbl",
            )
            cur_str = ",".join(map(str, self.current)) or "(none)"
            yield Label(
                f"Now: {cur_str}  ·  Available: 0–{self.total - 1}",
                classes="lbl",
            )
            yield Input(
                placeholder="e.g. 0,1,2,3  or  0-7,16-19",
                id="cores",
            )
            yield Button("Cancel",    id="cancel")
            yield Button("All cores", id="all")
            yield Button("Apply",     variant="warning", id="apply")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        if event.button.id == "all":
            self.dismiss(list(range(self.total)))
            return
        # Apply path
        raw = self.query_one("#cores", Input).value.strip()
        try:
            cores = self._parse_cores(raw)
        except ValueError as e:
            self.app.notify(f"Invalid: {e}", severity="error", timeout=3)
            return
        if not cores:
            self.app.notify(
                "Pick at least one core.", severity="error", timeout=2,
            )
            return
        bad = [c for c in cores if c < 0 or c >= self.total]
        if bad:
            self.app.notify(
                f"Out of range: {bad}", severity="error", timeout=3,
            )
            return
        self.dismiss(cores)

    @staticmethod
    def _parse_cores(raw: str) -> list[int]:
        """Parse '0,1,2-7,16-19' → [0, 1, 2, 3, 4, 5, 6, 7, 16, 17, 18, 19]."""
        if not raw:
            return []
        out: set[int] = set()
        for token in raw.split(","):
            token = token.strip()
            if not token:
                continue
            if "-" in token:
                lo, hi = token.split("-", 1)
                out.update(range(int(lo), int(hi) + 1))
            else:
                out.add(int(token))
        return sorted(out)


class HardwareScreen(Screen):
    """Full-screen hardware inventory. Scrollable when content overflows."""

    DEFAULT_CSS = """
    HardwareScreen { background: #0b0d10; }
    HardwareScreen ScrollableContainer {
        padding: 1 2;
    }
    HardwareScreen Static#hw-report {
        color: #e6e8eb;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("i",      "app.pop_screen", "Back"),
        Binding("q",      "app.pop_screen", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ScrollableContainer():
            yield Static(self._safe_report(), id="hw-report")
        yield Footer()

    @staticmethod
    def _safe_report() -> str:
        try:
            return hardware.report()
        except Exception as e:
            return f"[red]hardware probe failed:[/red] {e}"


class TrendsScreen(Screen):
    """Live line charts for the metrics in HISTORY.

    Each row is one Static label + one textual_plotext.PlotextPlot.
    The label is updated every tick with the current value, min, and
    max from the buffer (so absolute readings are still visible) and
    the chart is redrawn with a braille-marker line — a real line
    chart rather than the v0.0.7-style sparkline bars.

    Per-series colours: CPU cyan · GPU green · memory blue ·
    temperatures red · power magenta. plotext takes named colours
    directly via plt.plot(color=...), so no CSS plumbing this time.
    """

    DEFAULT_CSS = """
    TrendsScreen { background: #0b0d10; }
    TrendsScreen ScrollableContainer { padding: 1 2; }

    TrendsScreen Static.trend-label {
        margin: 1 1 0 1;
        height: 1;
        color: #e6e8eb;
    }
    TrendsScreen PlotextPlot {
        margin: 0 1 0 1;
        height: 7;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("t",      "app.pop_screen", "Back"),
        Binding("q",      "app.pop_screen", "Back"),
    ]

    # (history-key, widget-base-id, human label, plotext-colour, unit)
    _SERIES_CORE: list[tuple[str, str, str, str, str]] = [
        ("cpu.pct",  "cpu-pct",  "CPU usage", "cyan",    "%"),
        ("cpu.temp", "cpu-temp", "CPU temp",  "red",     "°C"),
        ("mem.pct",  "mem-pct",  "Memory",    "blue",    "%"),
    ]

    def _series(self) -> list[tuple[str, str, str, str, str]]:
        series = list(self._SERIES_CORE)
        for i in range(gpu.device_count()):
            series += [
                (f"gpu{i}.util",  f"gpu{i}-util",
                 f"GPU{i} util",  "green",   "%"),
                (f"gpu{i}.temp",  f"gpu{i}-temp",
                 f"GPU{i} temp",  "red",     "°C"),
                (f"gpu{i}.power", f"gpu{i}-power",
                 f"GPU{i} power", "magenta", "W"),
            ]
        return series

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ScrollableContainer():
            for _key, wid, _label, _colour, _unit in self._series():
                yield Static("", id=f"label-{wid}", classes="trend-label")
                yield PlotextPlot(id=f"plot-{wid}")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "wattson"
        self.sub_title = "trends · last 60 s"
        self._refresh()
        self.set_interval(1.0, self._refresh)

    @staticmethod
    def _fmt(value: float, unit: str) -> str:
        # Whole numbers for %, °C, W — one decimal would be noise on
        # a 1 Hz sample stream and hurts column alignment.
        return f"{value:>5.0f} {unit}"

    def _refresh(self) -> None:
        for key, wid, label, colour, unit in self._series():
            data = HISTORY.get(key)
            cap = HISTORY.capacity
            # ---- Label ----
            try:
                lab = self.query_one(f"#label-{wid}", Static)
            except Exception:
                continue
            if data:
                current = data[-1]
                mn = min(data)
                mx = max(data)
                lab.update(
                    f"[bold #e6e8eb]{label:<10}[/]"
                    f"  now [bold #00e5ff]{self._fmt(current, unit)}[/]"
                    f"  [#6b7480]· min {self._fmt(mn, unit)}"
                    f"  · max {self._fmt(mx, unit)}"
                    f"  · {len(data)}/{cap} s[/]"
                )
            else:
                lab.update(
                    f"[bold #e6e8eb]{label:<10}[/]  [#6b7480]no data yet[/]"
                )
            # ---- Plot ----
            try:
                plot = self.query_one(f"#plot-{wid}", PlotextPlot)
                plot.plt.clear_figure()
                if data:
                    xs = list(range(len(data)))
                    plot.plt.plot(
                        xs, data,
                        marker="braille",
                        color=colour,
                    )
                    plot.plt.theme("dark")
                    # Keep the axes around — they make absolute values
                    # interpretable — but drop labels to save vertical
                    # space (the label widget above already names it).
                    plot.plt.xlabel("")
                    plot.plt.ylabel("")
                plot.refresh()
            except Exception:
                continue


class GPUDrillScreen(Screen):
    """Per-GPU drill-down: hardware info + live metrics + per-metric
    line charts + processes filtered to this GPU.

    Opened by the `g` binding on the main dashboard. For multi-GPU rigs
    a future picker modal will let you choose the index; for now this
    always opens GPU 0.
    """

    DEFAULT_CSS = """
    GPUDrillScreen { background: #0b0d10; }
    GPUDrillScreen ScrollableContainer { padding: 1 2; }
    GPUDrillScreen Static.section-title {
        color: #00e5ff;
        text-style: bold;
        margin: 1 0 0 0;
    }
    GPUDrillScreen Static.section {
        color: #e6e8eb;
    }
    GPUDrillScreen Static.drill-label {
        margin: 1 0 0 0;
        height: 1;
        color: #e6e8eb;
    }
    GPUDrillScreen PlotextPlot {
        height: 6;
        margin: 0 0 0 0;
    }
    GPUDrillScreen DataTable {
        height: 8;
        margin: 1 0 0 0;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("g",      "app.pop_screen", "Back"),
        Binding("q",      "app.pop_screen", "Back"),
    ]

    _METRICS = (
        ("util",     "Util %",   "green"),
        ("temp",     "Temp °C",  "red"),
        ("power",    "Power W",  "magenta"),
        ("vram_pct", "VRAM %",   "yellow"),
    )

    def __init__(self, gpu_idx: int) -> None:
        super().__init__()
        self.gpu_idx = gpu_idx
        self._hw_done = False

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ScrollableContainer():
            yield Static("Hardware", classes="section-title")
            yield Static("loading…", id="drill-hw", classes="section")
            yield Static("Current", classes="section-title")
            yield Static("loading…", id="drill-current", classes="section")
            yield Static("History (last 60 s)", classes="section-title")
            for metric, label, _colour in self._METRICS:
                yield Static(label, id=f"drill-lbl-{metric}",
                             classes="drill-label")
                yield PlotextPlot(id=f"drill-plot-{metric}")
            yield Static("Processes on this GPU", classes="section-title")
            yield DataTable(id="drill-procs")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "wattson"
        self.sub_title = f"GPU{self.gpu_idx} drill-down"
        try:
            table = self.query_one("#drill-procs", DataTable)
            table.cursor_type = "row"
            table.zebra_stripes = True
            table.add_columns("PID", "NAME", "VRAM MB", "CPU%", "MEM MB")
        except Exception:
            pass
        self._refresh()
        self.set_interval(1.0, self._refresh)

    def _refresh(self) -> None:
        self._refresh_hw()
        self._refresh_current()
        self._refresh_plots()
        self._refresh_procs()

    def _refresh_hw(self) -> None:
        if self._hw_done:
            return
        try:
            target = self.query_one("#drill-hw", Static)
        except Exception:
            return
        info = gpu.device_info(self.gpu_idx)
        if info is None:
            target.update("(GPU info unavailable)")
            return
        lines = [
            f"[bold]{info.get('name', '?')}[/bold]",
            f"UUID:    {info.get('uuid', 'n/a')}",
            f"Serial:  {info.get('serial', 'n/a')}",
        ]
        if "pcie" in info:
            lines.append(f"PCIe:    {info['pcie']}")
        target.update("\n".join(lines))
        self._hw_done = True

    def _refresh_current(self) -> None:
        try:
            target = self.query_one("#drill-current", Static)
        except Exception:
            return
        m = gpu.metrics()
        idx = self.gpu_idx
        util = m.get(f"gpu{idx}.util")
        mem_bw = m.get(f"gpu{idx}.mem_bw")
        temp = m.get(f"gpu{idx}.temp")
        power = m.get(f"gpu{idx}.power")
        vram_pct = m.get(f"gpu{idx}.vram_pct")
        if util is None and temp is None:
            target.update("(no live metrics yet)")
            return
        head_parts = []
        if util is not None:
            head_parts.append(f"Util: {util:>3.0f}%")
        if mem_bw is not None:
            head_parts.append(f"MemBW: {mem_bw:>3.0f}%")
        if temp is not None:
            head_parts.append(f"Temp: {temp:>3.0f}°C")
        lines = ["  ·  ".join(head_parts)]
        if vram_pct is not None:
            lines.append(f"VRAM:  {vram_pct:>5.1f}%")
        if power is not None:
            lines.append(f"Power: {power:>5.1f} W")
        masks = gpu.throttle_masks()
        mask = masks.get(idx, 0)
        if mask:
            lines.append(
                f"[yellow]Throttle:[/yellow] {gpu.throttle_text(mask)}"
            )
        target.update("\n".join(lines))

    def _refresh_plots(self) -> None:
        idx = self.gpu_idx
        for metric, _label, colour in self._METRICS:
            key = f"gpu{idx}.{metric}"
            data = HISTORY.get(key)
            try:
                plot = self.query_one(
                    f"#drill-plot-{metric}", PlotextPlot,
                )
                plot.plt.clear_figure()
                if data:
                    plot.plt.plot(
                        list(range(len(data))), data,
                        marker="braille", color=colour,
                    )
                    plot.plt.theme("dark")
                plot.refresh()
            except Exception:
                continue

    def _refresh_procs(self) -> None:
        try:
            table = self.query_one("#drill-procs", DataTable)
        except Exception:
            return
        rows = processes.snapshot(limit=50)
        gpu_rows = [r for r in rows if r.get("gpu_idx") == self.gpu_idx]
        cursor = table.cursor_row
        table.clear()
        for r in gpu_rows:
            try:
                vram_mb = r.get("vram_mb")
                vram = f"{vram_mb:.0f}" if vram_mb is not None else "—"
                table.add_row(
                    Text(str(r["pid"])),
                    Text(r["name"][:30]),
                    Text(vram),
                    Text(f"{r['cpu_pct']:.1f}"),
                    Text(f"{r['mem_mb']:.0f}"),
                )
            except Exception:
                continue
        if 0 <= cursor < table.row_count:
            table.move_cursor(row=cursor)


class WatchdogScreen(Screen):
    """Tails the watchdog JSONL log; refreshes every 2 s.

    Uses one Static composed inside the ScrollableContainer (same pattern
    as HardwareScreen). Earlier versions yielded an empty container and
    `mount()`-ed Static widgets dynamically — Textual's render pipeline
    didn't handle that cleanly (visual = None → AttributeError on
    `render_strips`). `update()` on a single child is what works.
    """

    DEFAULT_CSS = """
    WatchdogScreen { background: #0b0d10; }
    WatchdogScreen ScrollableContainer { padding: 1 2; }
    WatchdogScreen Static#wd-content {
        color: #e6e8eb;
        padding: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("w",      "app.pop_screen", "Back"),
        Binding("q",      "app.pop_screen", "Back"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ScrollableContainer():
            yield Static("Loading watchdog log…", id="wd-content")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "wattson"
        self.sub_title = f"watchdog · {WATCHDOG.log_path}"
        self._refresh_log()
        self.set_interval(2.0, self._refresh_log)

    def _refresh_log(self) -> None:
        # NB: do not name this `_render` — that's a Textual Widget
        # internal that returns the widget's visual. Shadowing it with
        # a method that returns None blanks the screen and crashes the
        # render pipeline with AttributeError on `render_strips`.
        try:
            content = self.query_one("#wd-content", Static)
        except Exception:
            return
        events = WATCHDOG.recent_events(limit=200)
        if not events:
            content.update(
                "[#6b7480]No events logged yet.\n\n"
                "Thresholds: GPU > 85°C, CPU > 90°C, mem > 90 %, "
                "VRAM > 90 %, any active throttle reason.\n\n"
                f"Log file: {WATCHDOG.log_path}[/]"
            )
            return
        # Most recent first so you don't have to scroll
        lines: list[str] = []
        for ev in reversed(events):
            sev = ev.get("severity", "?")
            ts = ev.get("ts", "")
            msg = ev.get("message", "")
            colour = {"crit": "red", "warn": "yellow"}.get(sev, "white")
            lines.append(
                f"[{colour}]{sev.upper():<4}[/{colour}]  "
                f"{ts}  {msg}"
            )
        content.update("\n".join(lines))


class WattsonApp(App):
    CSS = """
    Screen { background: #0b0d10; }

    #stats-grid {
        grid-size: 2 2;
        grid-gutter: 1 1;
        padding: 1 1;
        height: 23;
    }

    StatPanel {
        border: round #38bdf8;
        padding: 0 2;
        color: #e6e8eb;
    }

    ProcessTable {
        height: 1fr;
        margin: 0 1 1 1;
        border: round #38bdf8;
    }

    Header { background: #14171c; }
    Footer { background: #14171c; }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit"),
        Binding("r", "refresh", "Refresh"),
        Binding("k", "kill", "Kill"),
        Binding("n", "priority", "Nice/Prio"),
        Binding("a", "affinity", "Affinity"),
        Binding("p", "power_limit", "Power"),
        Binding("g", "gpu_drill", "GPU drill"),
        Binding("i", "hardware", "Hardware"),
        Binding("t", "trends", "Trends"),
        Binding("w", "watchdog", "Watchdog"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical():
            with Grid(id="stats-grid"):
                yield StatPanel("CPU", cpu.snapshot, id="cpu-panel")
                yield StatPanel("GPU", gpu.snapshot, id="gpu-panel")
                yield StatPanel("Memory", memory.snapshot, id="mem-panel")
                yield StatPanel("Disk", disk.snapshot, id="disk-panel")
            yield ProcessTable(id="processes-table")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "wattson"
        self.sub_title = "your machine's personal assistant"
        # De-dup toasts: each `_notify_once(key, ...)` fires at most once.
        self._notified: set[str] = set()
        # v0.0.15 cadence: 2 s light + 3 s processes (was 1 s / 2 s).
        # User reported persistent lag despite all the v0.0.11-v0.0.14
        # caching. Halving the refresh rate is the most reliable way to
        # make key events feel instant — slightly staler data on a
        # system monitor is a trade most users gladly take.
        self._refresh_light()
        self._refresh_processes()
        self.set_interval(2.0, self._refresh_light)
        self.set_interval(3.0, self._refresh_processes)

    def action_refresh(self) -> None:
        self._refresh_all()

    def action_hardware(self) -> None:
        self.push_screen(HardwareScreen())

    def action_trends(self) -> None:
        self.push_screen(TrendsScreen())

    def action_watchdog(self) -> None:
        self.push_screen(WatchdogScreen())

    def action_kill(self) -> None:
        table = self.query_one("#processes-table", ProcessTable)
        row = table.selected()
        if row is None:
            self.notify("No process selected.", severity="warning", timeout=2)
            return
        self.push_screen(
            ConfirmKill(row["pid"], row["name"]),
            self._make_kill_callback(row["pid"]),
        )

    def _make_kill_callback(self, pid: int):
        def cb(confirmed: bool | None) -> None:
            if not confirmed:
                return
            result = processes.terminate(pid)
            severity = "warning" if result["ok"] else "error"
            self.notify(result["message"], severity=severity, timeout=3)
            self._refresh_all()
        return cb

    def action_priority(self) -> None:
        table = self.query_one("#processes-table", ProcessTable)
        row = table.selected()
        if row is None:
            self.notify("No process selected.", severity="warning", timeout=2)
            return
        self.push_screen(
            SetPriority(row["pid"], row["name"]),
            self._make_priority_callback(row["pid"]),
        )

    def _make_priority_callback(self, pid: int):
        def cb(level: str | None) -> None:
            if not level:
                return
            result = processes.set_priority(pid, level)
            severity = "warning" if result["ok"] else "error"
            self.notify(result["message"], severity=severity, timeout=3)
            self._refresh_all()
        return cb

    def action_affinity(self) -> None:
        table = self.query_one("#processes-table", ProcessTable)
        row = table.selected()
        if row is None:
            self.notify(
                "No process selected.", severity="warning", timeout=2,
            )
            return
        info = processes.cpu_affinity_info(row["pid"])
        if info is None:
            self.notify(
                "CPU affinity unavailable (macOS or no permission).",
                severity="warning", timeout=3,
            )
            return
        self.push_screen(
            SetAffinity(
                row["pid"], row["name"],
                info["current"], info["total"],
            ),
            self._make_affinity_callback(row["pid"]),
        )

    def _make_affinity_callback(self, pid: int):
        def cb(cores: list[int] | None) -> None:
            if cores is None:
                return
            result = processes.set_affinity(pid, cores)
            severity = "warning" if result["ok"] else "error"
            self.notify(result["message"], severity=severity, timeout=4)
            self._refresh_all()
        return cb

    def action_gpu_drill(self) -> None:
        if gpu.device_count() <= 0:
            self.notify(
                "No NVIDIA GPU detected.",
                severity="warning", timeout=2,
            )
            return
        # GPU 0 by default. Multi-GPU picker is roadmapped.
        self.push_screen(GPUDrillScreen(0))

    def action_power_limit(self) -> None:
        # GPU0 by default. Multi-GPU selection comes with the per-GPU
        # drill-in screen.
        info = gpu.power_limit_info(0)
        if info is None:
            self.notify(
                "No NVIDIA GPU0 found (or driver doesn't expose limits).",
                severity="warning", timeout=3,
            )
            return
        self.push_screen(
            SetPowerLimit(0, info),
            self._make_power_callback(0),
        )

    def _make_power_callback(self, gpu_idx: int):
        def cb(watts: int | None) -> None:
            if watts is None:
                return
            result = gpu.set_power_limit(gpu_idx, watts)
            severity = "warning" if result["ok"] else "error"
            self.notify(result["message"], severity=severity, timeout=4)
        return cb

    def _notify_once(self, key: str, message: str, severity: str) -> None:
        """Toast `message` only the first time we see this `key` per session,
        so a recurring per-tick error doesn't spam the corner."""
        if key in self._notified:
            return
        self._notified.add(key)
        self.notify(message, severity=severity, timeout=8)

    def _refresh_all(self) -> None:
        """Compatibility shim — kill/priority/affinity/power-limit
        callbacks call this after a controlling action. Only kicks
        the process worker (which is what visibly changes after a
        kill); the light refresh runs on its own 2 s cadence and
        catches up within a tick. `_refresh_light` is now an async
        coroutine and would need scheduling boilerplate to call from
        a sync callback — not worth the noise for a UI nudge."""
        self._refresh_processes()

    async def _refresh_light(self) -> None:
        """Async sync-style refresh for stat panels + metrics + watchdog.

        Async because Textual's `set_interval` accepts coroutines, and
        `await asyncio.sleep(0)` between expensive ops yields to the
        event loop — that's what lets a `t` / `g` / `k` keypress jump
        the queue instead of waiting for the whole refresh to finish.

        Cheap because:
          - cpu / memory / disk snapshots are fast (or cached in disk
            and CPU-temp cases);
          - gpu.snapshot / gpu.metrics / gpu.throttle_masks share the
            cached `_collect()` in gpu.py, so a single tick triggers
            at most one NVML round across all three.
        """
        import asyncio as _asyncio

        for panel in self.query(StatPanel):
            panel.refresh_body()
            await _asyncio.sleep(0)  # let key events through
        try:
            all_metrics: dict[str, float] = {}
            all_metrics.update(cpu.metrics())
            all_metrics.update(memory.metrics())
            all_metrics.update(gpu.metrics())
            HISTORY.add_many(all_metrics)
            await _asyncio.sleep(0)
            WATCHDOG.check(all_metrics, gpu.throttle_masks())
            count = WATCHDOG.session_count
            base = "your machine's personal assistant"
            new_sub = f"{base} · ⚠ {count}" if count else base
            # Only assign when changed; Textual still does layout work
            # on identical assignments.
            if self.sub_title != new_sub:
                self.sub_title = new_sub
        except Exception as e:
            self._notify_once(
                "metrics-error",
                f"metrics/watchdog error: {type(e).__name__}: {e}",
                "error",
            )

    @work(exclusive=True, thread=True, group="processes")
    def _refresh_processes(self) -> None:
        """Heavy path on a worker thread.

        `processes.snapshot()` iterates every PID and on Windows still
        spends real time even after the two-phase optimisation — far
        more than the input loop can tolerate at 1 Hz. Running it in a
        thread keeps key events instant. `exclusive=True` guarantees
        only one snapshot is in flight at a time.
        """
        try:
            rows = processes.snapshot(limit=20)
        except Exception as e:
            self.call_from_thread(
                self._notify_once,
                "processes-error",
                f"processes probe error: {type(e).__name__}: {e}",
                "error",
            )
            return
        self.call_from_thread(self._apply_process_rows, rows)

    def _apply_process_rows(self, rows: list[dict]) -> None:
        """Main-thread sink for the worker's results."""
        try:
            self.query_one(
                "#processes-table", ProcessTable
            ).refresh_rows(rows)
        except Exception:
            pass


def main() -> None:
    WattsonApp().run()


if __name__ == "__main__":
    main()
