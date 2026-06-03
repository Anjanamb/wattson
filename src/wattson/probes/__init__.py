"""System probes.

Most probes expose `snapshot() -> str` (rendered directly into a `StatPanel`);
`processes.snapshot()` returns `list[ProcessRow]` (structured data for the
DataTable); `hardware.report()` returns a Rich-markup multi-section string
rendered by the HardwareScreen.

Probes are deliberately stateless and synchronous unless they need to carry
state across refreshes (see `processes.ProcessProbe`).
"""

from . import cpu, disk, gpu, hardware, memory, processes

__all__ = ["cpu", "disk", "gpu", "hardware", "memory", "processes"]
