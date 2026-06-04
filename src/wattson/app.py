"""wattson TUI — 4-stat dashboard, GPU-aware process table with criticality
markers, kill action, hardware-inventory screen (`i`), live trends with
sparklines (`t`), and a watchdog that logs throttle / OOM / hot-temp
events to disk (`w` opens the log)."""

from __future__ import annotations

from rich.text import Text
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
    Sparkline,
    Static,
)

from .history import HISTORY
from .probes import cpu, disk, gpu, hardware, memory, processes
from .watchdog import WATCHDOG


class StatPanel(Static):
    """One resource panel; re-renders when `body` changes."""

    body: reactive[str] = reactive("loading…")

    def __init__(self, title: str, body_getter, **kwargs) -> None:
        super().__init__(**kwargs)
        self._title = title
        self._getter = body_getter

    def render(self) -> str:
        return f"[bold cyan]{self._title}[/bold cyan]\n\n{self.body}"

    def refresh_body(self) -> None:
        try:
            self.body = self._getter()
        except Exception as e:  # never let one bad probe kill the loop
            self.body = f"[red]probe error:[/red] {e}"


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
        super().__init__()
        self.pid = pid
        self.name = name

    def compose(self) -> ComposeResult:
        with Grid():
            yield Label(
                f"Kill PID {self.pid}  ({self.name})?",
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
        super().__init__()
        self.pid = pid
        self.name = name

    def compose(self) -> ComposeResult:
        with Grid():
            yield Label(
                f"Set priority — PID {self.pid}  ({self.name})",
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
            self.app.notify("Enter a whole number.", severity="error", timeout=2)
            return
        if not (self.min_w <= value <= self.max_w):
            self.app.notify(
                f"Must be {self.min_w}-{self.max_w} W.",
                severity="error", timeout=2,
            )
            return
        self.dismiss(value)


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
    """Live sparklines for the metrics in HISTORY.

    Reads metric series from the singleton History buffer on a 1-second
    timer, while the parent app keeps populating it via _refresh_all().
    GPU rows are emitted dynamically based on `gpu.device_count()`.
    """

    DEFAULT_CSS = """
    TrendsScreen { background: #0b0d10; }
    TrendsScreen ScrollableContainer {
        padding: 1 2;
    }
    TrendsScreen Label.trend-label {
        color: #9aa3ad;
        margin: 1 1 0 1;
        height: 1;
    }
    TrendsScreen Sparkline {
        margin: 0 1 0 1;
        height: 3;
    }
    """

    BINDINGS = [
        Binding("escape", "app.pop_screen", "Back"),
        Binding("t",      "app.pop_screen", "Back"),
        Binding("q",      "app.pop_screen", "Back"),
    ]

    # (history-key, widget-id, human label)
    _SERIES_CORE = [
        ("cpu.pct",  "spark-cpu-pct",  "CPU usage  (%)"),
        ("cpu.temp", "spark-cpu-temp", "CPU temp   (°C)"),
        ("mem.pct",  "spark-mem-pct",  "Memory     (%)"),
    ]

    def _series(self) -> list[tuple[str, str, str]]:
        series = list(self._SERIES_CORE)
        for i in range(gpu.device_count()):
            series += [
                (f"gpu{i}.util",  f"spark-gpu{i}-util",
                 f"GPU{i} util   (%)"),
                (f"gpu{i}.temp",  f"spark-gpu{i}-temp",
                 f"GPU{i} temp   (°C)"),
                (f"gpu{i}.power", f"spark-gpu{i}-power",
                 f"GPU{i} power  (W)"),
            ]
        return series

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with ScrollableContainer():
            for _key, wid, label in self._series():
                yield Label(label, classes="trend-label")
                yield Sparkline([0.0], id=wid)
        yield Footer()

    def on_mount(self) -> None:
        self.title = "wattson"
        self.sub_title = "trends · last 60 s"
        self._refresh()
        self.set_interval(1.0, self._refresh)

    def _refresh(self) -> None:
        for key, wid, _label in self._series():
            data = HISTORY.get(key)
            try:
                sparkline = self.query_one(f"#{wid}", Sparkline)
                # Sparkline rendering doesn't like an empty list — feed it
                # a single zero so the widget paints a flat baseline.
                sparkline.data = data if data else [0.0]
            except Exception:
                # The widget might not be mounted yet; tick again next time.
                continue


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
        self._render()
        self.set_interval(2.0, self._render)

    def _render(self) -> None:
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
        Binding("p", "power_limit", "Power"),
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
        self._refresh_all()
        self.set_interval(1.0, self._refresh_all)

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
        # Stat panels — each StatPanel.refresh_body already has its own
        # try/except, so one bad panel can't break the others.
        for panel in self.query(StatPanel):
            panel.refresh_body()

        # Process table — isolated try block so a snapshot/render failure
        # does not also kill history+watchdog below.
        try:
            rows = processes.snapshot(limit=20)
            self.query_one(
                "#processes-table", ProcessTable
            ).refresh_rows(rows)
        except Exception as e:
            self._notify_once(
                "processes-error",
                f"processes probe error: {type(e).__name__}: {e}",
                "error",
            )

        # History + watchdog — independent of the process table.
        try:
            all_metrics: dict[str, float] = {}
            all_metrics.update(cpu.metrics())
            all_metrics.update(memory.metrics())
            all_metrics.update(gpu.metrics())
            HISTORY.add_many(all_metrics)
            WATCHDOG.check(all_metrics, gpu.throttle_masks())
            count = WATCHDOG.session_count
            base = "your machine's personal assistant"
            self.sub_title = f"{base} · ⚠ {count}" if count else base
        except Exception as e:
            self._notify_once(
                "metrics-error",
                f"metrics/watchdog error: {type(e).__name__}: {e}",
                "error",
            )


def main() -> None:
    WattsonApp().run()


if __name__ == "__main__":
    main()
