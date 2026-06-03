"""Watchdog — log alerts when training jobs throttle, OOM, or run hot.

Each refresh tick the TUI hands the watchdog a `metrics` dict plus the
per-GPU `throttle_masks` dict; it decides which thresholds are crossed
and appends JSON-line events to `~/.wattson/events.jsonl`.

Rate limit: at most one event per category per `RATE_LIMIT_SEC` (60 s).
That stops a sustained throttle from flooding the log while still
giving you a clear timeline of when each issue started.

The `w` keybinding in the TUI opens `WatchdogScreen` which tails the
JSONL file. Nothing in the watchdog *requires* the TUI — `WATCHDOG`
works headless too, which is the whole point.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

DEFAULT_LOG_PATH = Path.home() / ".wattson" / "events.jsonl"
RATE_LIMIT_SEC = 60.0

# Thresholds — tunable, not yet exposed via config.
GPU_TEMP_WARN = 85.0
GPU_TEMP_CRIT = 95.0
CPU_TEMP_WARN = 90.0
CPU_TEMP_CRIT = 100.0
MEM_PCT_WARN = 90.0
MEM_PCT_CRIT = 95.0
VRAM_PCT_WARN = 90.0
VRAM_PCT_CRIT = 98.0


class Watchdog:
    def __init__(self, log_path: Path = DEFAULT_LOG_PATH) -> None:
        self.log_path = log_path
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._last_event_at: dict[str, float] = {}
        self._session_count = 0

    @property
    def session_count(self) -> int:
        return self._session_count

    def check(
        self,
        metrics: dict[str, float],
        throttle_masks: Optional[dict[int, int]] = None,
    ) -> int:
        """Inspect metrics + throttle state; log any threshold crossings.
        Returns the number of events logged this tick (post rate-limit)."""
        events: list[tuple[str, str, str, dict]] = []

        # CPU temperature
        cpu_t = metrics.get("cpu.temp")
        if cpu_t is not None:
            if cpu_t >= CPU_TEMP_CRIT:
                events.append(("crit", "cpu.temp",
                              f"CPU at {cpu_t:.0f}°C", {"value": cpu_t}))
            elif cpu_t >= CPU_TEMP_WARN:
                events.append(("warn", "cpu.temp",
                              f"CPU at {cpu_t:.0f}°C", {"value": cpu_t}))

        # System memory
        mem_p = metrics.get("mem.pct")
        if mem_p is not None:
            if mem_p >= MEM_PCT_CRIT:
                events.append(("crit", "mem.pct",
                              f"System memory at {mem_p:.0f}%",
                              {"value": mem_p}))
            elif mem_p >= MEM_PCT_WARN:
                events.append(("warn", "mem.pct",
                              f"System memory at {mem_p:.0f}%",
                              {"value": mem_p}))

        # Per-GPU temperature + VRAM
        for key, value in metrics.items():
            if key.endswith(".temp") and key.startswith("gpu"):
                gpu_id = key.split(".", 1)[0]
                if value >= GPU_TEMP_CRIT:
                    events.append(("crit", f"{gpu_id}.temp",
                                  f"{gpu_id} at {value:.0f}°C",
                                  {"value": value}))
                elif value >= GPU_TEMP_WARN:
                    events.append(("warn", f"{gpu_id}.temp",
                                  f"{gpu_id} at {value:.0f}°C",
                                  {"value": value}))
            elif key.endswith(".vram_pct"):
                gpu_id = key.split(".", 1)[0]
                if value >= VRAM_PCT_CRIT:
                    events.append(("crit", f"{gpu_id}.vram",
                                  f"{gpu_id} VRAM at {value:.0f}%",
                                  {"value": value}))
                elif value >= VRAM_PCT_WARN:
                    events.append(("warn", f"{gpu_id}.vram",
                                  f"{gpu_id} VRAM at {value:.0f}%",
                                  {"value": value}))

        # Active GPU throttle reasons
        if throttle_masks:
            # Local import keeps watchdog independent of NVML at import time
            from .probes.gpu import throttle_text

            for gpu_idx, mask in throttle_masks.items():
                if not mask:
                    continue
                reasons = throttle_text(mask)
                if reasons:
                    events.append((
                        "warn", f"gpu{gpu_idx}.throttle",
                        f"gpu{gpu_idx} throttling: {reasons}",
                        {"mask": mask, "reasons": reasons},
                    ))

        # Log, with per-category rate limiting
        logged = 0
        for sev, cat, msg, details in events:
            if self._should_log(cat):
                self._write(sev, cat, msg, details)
                logged += 1
        self._session_count += logged
        return logged

    def _should_log(self, category: str) -> bool:
        now = time.time()
        last = self._last_event_at.get(category, 0.0)
        if now - last < RATE_LIMIT_SEC:
            return False
        self._last_event_at[category] = now
        return True

    def _write(
        self,
        severity: str,
        category: str,
        message: str,
        details: dict,
    ) -> None:
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "severity": severity,
            "event": category,
            "message": message,
            "details": details,
        }
        try:
            with self.log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except OSError:
            # Best-effort; never let a disk hiccup crash the TUI loop.
            pass

    def recent_events(self, limit: int = 100) -> list[dict]:
        """Read up to the last `limit` events from the log file."""
        try:
            with self.log_path.open("r", encoding="utf-8") as f:
                lines = f.readlines()
        except (OSError, FileNotFoundError):
            return []
        out: list[dict] = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out


# Module-level singleton (the app uses this).
WATCHDOG = Watchdog()
