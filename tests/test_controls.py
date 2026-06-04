"""Smoke tests for the v0.0.7 control actions.

These don't actually flip priorities or change power limits — we only
verify input validation and that the functions return the shape the
TUI relies on, including graceful failure paths.
"""

from __future__ import annotations

from wattson.probes import gpu, processes


def test_set_priority_rejects_unknown_level():
    r = processes.set_priority(1, "ludicrous")
    assert r == {"ok": False, "message": "Unknown priority level 'ludicrous'"}


def test_set_priority_returns_struct_on_dead_pid():
    # PID -1 should never exist; psutil raises NoSuchProcess, we wrap.
    r = processes.set_priority(-1, "normal")
    assert isinstance(r, dict)
    assert set(r.keys()) == {"ok", "message"}
    assert r["ok"] is False


def test_gpu_power_limit_info_signature():
    # Returns None when no driver / no GPU; a dict with the expected
    # keys when present. Either is valid for this smoke test.
    info = gpu.power_limit_info(0)
    if info is None:
        return
    assert {"name", "current_w", "cap_w", "min_w", "max_w"} <= info.keys()
    assert info["min_w"] <= info["cap_w"] <= info["max_w"]


def test_gpu_set_power_limit_struct_on_no_driver():
    # Even when NVML is unavailable, the function must return the same
    # shape (never raise) — that's the TUI's contract.
    r = gpu.set_power_limit(0, 50)
    assert isinstance(r, dict)
    assert set(r.keys()) == {"ok", "message"}
