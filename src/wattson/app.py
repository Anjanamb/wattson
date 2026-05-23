"""wattson TUI — 4-stat dashboard + GPU-aware process table."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Grid, Vertical
from textual.reactive import reactive
from textual.widgets import DataTable, Footer, Header, Static

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
    """Top processes by GPU + CPU usage. Refreshed by the parent App."""

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


class WattsonApp(App):
    CSS = """
    Screen { background: #0b0d10; }

    #stats-grid {
        grid-size: 2 2;
        grid-gutter: 1 1;
        padding: 1 1;
        height: 18;
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
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
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
