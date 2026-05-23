"""wattson TUI — minimal 4-panel dashboard."""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Grid
from textual.reactive import reactive
from textual.widgets import Footer, Header, Static

from .probes import cpu, disk, gpu, memory


class StatPanel(Static):
    """A single resource panel with a title and a body that re-renders on `body` change."""

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


class WattsonApp(App):
    CSS = """
    Screen { background: #0b0d10; }
    Grid {
        grid-size: 2 2;
        grid-gutter: 1 1;
        padding: 1 2;
    }
    StatPanel {
        border: round #38bdf8;
        padding: 1 2;
        color: #e6e8eb;
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
        with Grid():
            yield StatPanel("CPU", cpu.snapshot, id="cpu-panel")
            yield StatPanel("GPU", gpu.snapshot, id="gpu-panel")
            yield StatPanel("Memory", memory.snapshot, id="mem-panel")
            yield StatPanel("Disk", disk.snapshot, id="disk-panel")
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


def main() -> None:
    WattsonApp().run()


if __name__ == "__main__":
    main()
