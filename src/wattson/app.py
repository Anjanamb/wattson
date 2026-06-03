"""wattson TUI — 4-stat dashboard + GPU-aware process table + kill action."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Grid, Vertical
from textual.reactive import reactive
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, Footer, Header, Label, Static

from .probes import cpu, disk, gpu, memory, processes


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
            self.add_row(
                str(r["pid"]),
                r["name"][:24],
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
        Binding("k", "kill", "Kill selected"),
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
            self.query_one("#processes-table", ProcessTable).refresh_rows(rows)
        except Exception:
            # processes probe is best-effort; keep the rest of the UI alive
            pass


def main() -> None:
    WattsonApp().run()


if __name__ == "__main__":
    main()
