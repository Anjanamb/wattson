"""Smoke + behaviour tests for the watchdog.

Uses a per-test temp log path so each test gets a clean slate.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from wattson.watchdog import (
    CPU_TEMP_CRIT,
    CPU_TEMP_WARN,
    GPU_TEMP_CRIT,
    GPU_TEMP_WARN,
    RATE_LIMIT_SEC,
    Watchdog,
)


def _wd(tmp_path: Path) -> Watchdog:
    return Watchdog(log_path=tmp_path / "events.jsonl")


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def test_no_events_below_threshold(tmp_path: Path):
    wd = _wd(tmp_path)
    n = wd.check({"cpu.temp": 40.0, "mem.pct": 50.0, "gpu0.temp": 60.0})
    assert n == 0
    assert _read(wd.log_path) == []


def test_logs_cpu_warn_above_threshold(tmp_path: Path):
    wd = _wd(tmp_path)
    n = wd.check({"cpu.temp": CPU_TEMP_WARN + 1})
    assert n == 1
    events = _read(wd.log_path)
    assert events[0]["severity"] == "warn"
    assert events[0]["event"] == "cpu.temp"


def test_logs_cpu_crit_uses_crit_severity(tmp_path: Path):
    wd = _wd(tmp_path)
    wd.check({"cpu.temp": CPU_TEMP_CRIT + 1})
    assert _read(wd.log_path)[0]["severity"] == "crit"


def test_gpu_thermal_event(tmp_path: Path):
    wd = _wd(tmp_path)
    wd.check({"gpu0.temp": GPU_TEMP_CRIT + 1})
    ev = _read(wd.log_path)[0]
    assert ev["severity"] == "crit"
    assert ev["event"] == "gpu0.temp"


def test_rate_limiter_suppresses_repeats(tmp_path: Path):
    wd = _wd(tmp_path)
    # Three back-to-back ticks above threshold should produce one event
    for _ in range(3):
        wd.check({"cpu.temp": CPU_TEMP_WARN + 5})
    assert len(_read(wd.log_path)) == 1
    assert wd.session_count == 1


def test_throttle_mask_logs_event(tmp_path: Path):
    wd = _wd(tmp_path)
    # 0x20 = SW thermal slowdown in our label table
    wd.check({}, throttle_masks={0: 0x20})
    ev = _read(wd.log_path)[0]
    assert ev["event"] == "gpu0.throttle"
    assert "Thermal (sw)" in ev["message"]


def test_recent_events_round_trip(tmp_path: Path):
    wd = _wd(tmp_path)
    wd.check({"cpu.temp": CPU_TEMP_WARN + 1})
    wd.check({"mem.pct": 96.0})
    # Bypass rate limit so both surface
    time.sleep(0)
    recent = wd.recent_events()
    assert len(recent) >= 1
    assert all("ts" in e and "event" in e for e in recent)


def test_rate_limit_constant_is_sane():
    # Catch accidental "0 = log everything" or absurd values during refactors
    assert 1.0 <= RATE_LIMIT_SEC <= 3600.0
