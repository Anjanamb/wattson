"""wattson TUI — 4-stat dashboard, GPU-aware process table with criticality
markers, kill action, hardware-inventory screen (`i`), and a live trends
screen with sparklines for CPU / memory / per-GPU metrics (`t`)."""

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
    Label,
    Sparkline,
    Static,
)

from .history import HISTORY
from .probes import cpu, disk, gpu, hardware, memory, processes


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
            gi, vm = r["gpu_idx"], r["vram_mb"]
            gpu_str = f"#{gi}" if gi is not None else "—"
            vram_str = f"{vm:.0f}" if vm is not None else "—"
            critical = r.get("critical", False)
            marker = "★ " if critical else "  "
            name = Text(marker + r["name"][:22])
            if critical:
                name.stylize("bold cyan")
            self.add_row(
                str(r["pid"]),
                name,
                f"{r['cpu_pct']:.1f}",
                f"{r['mem_mb']:.0f}",
                gpu_str,
                vram_str,
                r["cmdline"],
            )
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
        Binding("i", "hardware", "Hardware"),
        Binding("t", "trends", "Trends"),
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
        self._refresh_all()
        self.set_interval(1.0, self._refresh_all)

    def action_refresh(self) -> None:
        self._refresh_all()

    def action_hardware(self) -> None:
        self.push_screen(HardwareScreen())

    def action_trends(self) -> None:
        self.push_screen(TrendsScreen())

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

    def _refresh_all(self) -> None:
        for panel in self.query(StatPanel):
            panel.refresh_body()
        try:
            rows = processes.snapshot(limit=20)
            self.query_one(
                "#processes-table", ProcessTable
            ).refresh_rows(rows)
        except Exception:
            # processes probe is best-effort; keep the rest of the UI alive
            pass
        # Feed the rolling history buffer used by TrendsScreen. Each probe
        # decides which scalars it surfaces; missing values are silently
        # dropped. Best-effort — must not crash the loop.
        try:
            HISTORY.add_many(cpu.metrics())
            HISTORY.add_many(memory.metrics())
            HISTORY.add_many(gpu.metrics())
        except Exception:
            pass


def main() -> None:
    WattsonApp().run()


if __name__ == "__main__":
    main()
