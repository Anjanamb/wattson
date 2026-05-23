"""Smoke tests for the probe modules.

These only check that snapshot() returns a non-empty string without exceptions,
which is the contract the TUI relies on. Detailed correctness checks (e.g.,
values within expected ranges) come later when probes carry state.
"""

from wattson.probes import cpu, disk, gpu, memory, processes


def test_cpu_snapshot_is_nonempty_string():
    out = cpu.snapshot()
    assert isinstance(out, str) and out.strip()


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
        assert {"pid", "name", "cpu_pct", "mem_mb", "gpu_idx", "vram_mb", "cmdline"} <= set(r.keys())
        assert isinstance(r["pid"], int)
