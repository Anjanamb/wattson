"""System probes — each module exposes `snapshot() -> str`.

Probes are deliberately stateless and synchronous for v0. As features grow
(history, deltas, throttling reasons), they'll evolve into classes that
carry state across refreshes.
"""

from . import cpu, disk, gpu, memory

__all__ = ["cpu", "disk", "gpu", "memory"]
