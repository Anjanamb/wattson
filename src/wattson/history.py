"""Rolling history of scalar metrics for the Trends screen.

A `History` is a dict of named ring buffers (default capacity 60 = one
minute at 1 Hz). Probes don't touch this directly — the app's refresh
loop pulls a single `metrics()` dict from each probe and pushes it via
`HISTORY.add_many(...)`. The TrendsScreen reads back through `get(key)`.

Why a custom singleton instead of `collections.deque` directly:
  - Auto-create buffers on first `add()` so probes don't have to register.
  - Single capacity so all sparklines have the same x-scale.
  - Trivial to unit-test (`reset()` for a clean state).
"""

from __future__ import annotations

from collections import deque


class History:
    def __init__(self, capacity: int = 60) -> None:
        self._capacity = capacity
        self._buffers: dict[str, deque[float]] = {}

    @property
    def capacity(self) -> int:
        return self._capacity

    def add(self, key: str, value: float) -> None:
        buf = self._buffers.get(key)
        if buf is None:
            buf = deque(maxlen=self._capacity)
            self._buffers[key] = buf
        buf.append(float(value))

    def add_many(self, items: dict[str, float]) -> None:
        for k, v in items.items():
            self.add(k, v)

    def get(self, key: str) -> list[float]:
        return list(self._buffers.get(key, []))

    def keys(self) -> list[str]:
        return list(self._buffers.keys())

    def reset(self) -> None:
        self._buffers.clear()


# Module-level singleton — capacity matches the 1 Hz refresh × 60 = 1 min view.
HISTORY = History(capacity=60)
