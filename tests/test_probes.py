"""Smoke tests for the probe modules.

These only check that snapshot() returns a non-empty string without exceptions,
which is the contract the TUI relies on. Detailed correctness checks (e.g.,
values within expected ranges) come later when probes carry state.
"""

from wattson.probes import cpu, disk, gpu, hardware, memory, processes


def test_cpu_snapshot_is_nonempty_string():
    out = cpu.snapshot()
    assert isinstance(out, str) and out.strip()


def test_cpu_snapshot_includes_temp_line():
    # v0.0.3+ — CPU panel always carries a Temp: line
    # (value may be "n/a" on Windows until WMI backend lands)
    assert "Temp:" in cpu.snapshot()


def test_memory_snapshot_is_nonempty_string():
    out = memory.snapshot()
    assert isinstance(out, str) and out.strip()


def test_disk_snapshot_is_string():
    # may be "no readable partitions" on hardened envs; still a string
    assert isinstance(disk.snapshot(), str)


def test_gpu_snapshot_is_string():
    # always returns a string — graceful "no GPU" message if NVML unavailable
    assert isinstance(gpu.snapshot(), str)


def test_processes_snapshot_is_list_of_rows():
    rows = processes.snapshot(limit=5)
    assert isinstance(rows, list)
    assert len(rows) <= 5
    if rows:
        r = rows[0]
        expected = {
            "pid", "name", "cpu_pct", "mem_mb", "gpu_idx",
            "vram_mb", "cmdline", "critical",  # critical added in v0.0.4
        }
        assert expected <= set(r.keys())
        assert isinstance(r["pid"], int)
        assert isinstance(r["critical"], bool)


def test_hardware_report_has_three_sections():
    # v0.0.4 — System / CPU / GPU(s) blocks always present
    rep = hardware.report()
    assert isinstance(rep, str)
    assert "System" in rep and "CPU" in rep and "GPU" in rep
