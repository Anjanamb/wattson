"""Rule tests for `wattson diagnose` — synthetic summaries, no GPU needed."""

from __future__ import annotations

import json

from wattson import diagnose
from wattson.cli import main


def _summary(**over) -> dict:
    s = {
        "duration_s": 10.0,
        "samples": 10,
        "cpu": {"logical_cores": 16, "avg_pct": 20.0, "hottest_core_avg_pct": 40.0,
                "iowait_pct": 0.5, "temp_c": 60.0, "load_avg_1m": 2.0},
        "memory": {"ram_pct": 40.0, "ram_avail_gb": 20.0, "swap_pct": 0.0,
                   "swap_rate_mb_s": 0.0},
        "disk_io": {"read_mb_s": 5.0, "write_mb_s": 1.0},
        "gpu_available": True,
        "gpus": [_gpu()],
        "disks": [{"mount": "/", "fstype": "ext4", "total_gb": 500.0,
                   "free_gb": 300.0, "used_pct": 40.0}],
        "processes": [],
    }
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(s.get(k), dict):
            s[k] = {**s[k], **v}
        else:
            s[k] = v
    return s


def _gpu(**over) -> dict:
    g = {"index": 0, "name": "Test GPU", "util_avg": 95.0, "util_min": 90.0,
         "util_max": 99.0, "mem_bw_avg": 50.0, "vram_used_mb": 4000.0,
         "vram_total_mb": 8000.0, "vram_pct": 50.0, "temp_max_c": 70.0,
         "power_avg_w": 100.0, "power_cap_w": 115.0, "throttle_fraction": {}}
    g.update(over)
    return g


def _proc(pid=100, cpu=100.0, vram=3000.0, gpu=0, mem=2000.0) -> dict:
    return {"pid": pid, "name": "python", "cpu_pct": cpu, "mem_mb": mem,
            "gpu_idx": gpu, "vram_mb": vram, "cmdline": "python train.py"}


def _ids(summary: dict) -> list[str]:
    return [f["id"] for f in diagnose.analyze(summary)]


def test_busy_gpu_is_healthy():
    assert _ids(_summary(processes=[_proc()])) == ["healthy"]


def test_no_gpu():
    ids = _ids(_summary(gpu_available=False, gpus=[]))
    assert "no_gpu" in ids and "healthy" in ids


def test_single_thread_starvation():
    s = _summary(gpus=[_gpu(util_avg=20.0)], processes=[_proc()],
                 cpu={"avg_pct": 7.0, "hottest_core_avg_pct": 99.0})
    assert _ids(s) == ["gpu_starved_single_thread"]


def test_cpu_bound_starvation():
    s = _summary(gpus=[_gpu(util_avg=30.0)], processes=[_proc()],
                 cpu={"avg_pct": 95.0, "hottest_core_avg_pct": 100.0})
    assert _ids(s) == ["gpu_starved_cpu"]


def test_io_bound_starvation_wins_over_cpu():
    s = _summary(gpus=[_gpu(util_avg=30.0)], processes=[_proc()],
                 cpu={"avg_pct": 95.0, "iowait_pct": 25.0})
    assert _ids(s) == ["gpu_starved_io"]


def test_underutilized_without_bottleneck():
    s = _summary(gpus=[_gpu(util_avg=40.0)], processes=[_proc()])
    ids = _ids(s)
    assert ids[0] == "gpu_underutilized"
    assert "healthy" in ids  # info-only => still "no problems"


def test_stale_process_offers_kill_needing_confirmation():
    s = _summary(gpus=[_gpu(util_avg=0.0)], processes=[_proc(pid=4242, cpu=0.1)])
    (f,) = [f for f in diagnose.analyze(s) if f["id"] == "stale_gpu_process"]
    assert f["actions"][0]["command"] == "wattson kill 4242"
    assert f["actions"][0]["requires_confirmation"] is True
    assert "gpu_starved_single_thread" not in _ids(s)


def test_idle_gpu_without_holders_is_quiet():
    assert _ids(_summary(gpus=[_gpu(util_avg=0.0, vram_pct=2.0)])) == ["healthy"]


def test_thermal_throttle_and_severity():
    s = _summary(gpus=[_gpu(temp_max_c=96.0, throttle_fraction={"Thermal (hw)": 0.5})],
                 processes=[_proc()])
    (f,) = [f for f in diagnose.analyze(s) if f["id"] == "gpu_thermal_throttle"]
    assert f["severity"] == "crit"


def test_power_cap_only_when_busy():
    busy = _summary(gpus=[_gpu(throttle_fraction={"PowerCap (sw)": 0.9})], processes=[_proc()])
    assert "gpu_power_capped" in _ids(busy)
    idle = _summary(gpus=[_gpu(util_avg=1.0, vram_pct=1.0,
                               throttle_fraction={"PowerCap (sw)": 0.9})])
    assert "gpu_power_capped" not in _ids(idle)


def test_vram_near_full():
    s = _summary(gpus=[_gpu(vram_pct=99.0)], processes=[_proc()])
    (f,) = [f for f in diagnose.analyze(s) if f["id"] == "vram_near_full"]
    assert f["severity"] == "crit"


def test_contention():
    s = _summary(processes=[_proc(pid=1), _proc(pid=2)])
    assert "gpu_contention" in _ids(s)


def test_memory_swap_disk_cpu_temp():
    s = _summary(
        memory={"ram_pct": 96.0, "swap_rate_mb_s": 20.0},
        disks=[{"mount": "/data", "fstype": "ext4", "total_gb": 100.0,
                "free_gb": 2.0, "used_pct": 98.0}],
        cpu={"temp_c": 95.0},
        processes=[_proc()],
    )
    ids = _ids(s)
    for want in ("ram_pressure", "swapping", "disk_nearly_full", "cpu_hot"):
        assert want in ids
    assert "healthy" not in ids


def test_findings_sorted_by_severity():
    s = _summary(gpus=[_gpu(vram_pct=99.0, util_avg=40.0)], processes=[_proc()],
                 memory={"ram_pct": 91.0})
    sev = [f["severity"] for f in diagnose.analyze(s)]
    assert sev == sorted(sev, key=lambda x: {"crit": 0, "warn": 1, "info": 2}[x])


def test_summarize_aggregates_ticks():
    tick = lambda u, mask_labels: {  # noqa: E731
        "cpu_per_core": [10.0, 90.0], "iowait": 1.0, "mem_pct": 50.0,
        "mem_avail_gb": 8.0,
        "gpus": {0: {"name": "G", "util": u, "mem_bw": 10.0, "vram_used": 2 * 1024**3,
                     "vram_total": 8 * 1024**3, "temp": 60.0, "power_w": 50.0,
                     "power_cap_w": 100.0, "throttle_labels": mask_labels}},
    }
    raw = {"elapsed_s": 2.0, "gpu_available": True,
           "ticks": [tick(20.0, ["Thermal (sw)"]), tick(40.0, [])],
           "processes": [], "disks": []}
    s = diagnose.summarize(raw)
    g = s["gpus"][0]
    assert g["util_avg"] == 30.0 and g["vram_pct"] == 25.0
    assert g["throttle_fraction"] == {"Thermal (sw)": 0.5}
    assert s["cpu"]["avg_pct"] == 50.0 and s["cpu"]["hottest_core_avg_pct"] == 90.0


def test_report_is_json_serialisable():
    rep = diagnose.report(_summary(processes=[_proc()]))
    assert json.loads(json.dumps(rep))["schema_version"] == diagnose.SCHEMA_VERSION
    assert "healthy" in diagnose.render_text(rep)


def test_cli_diagnose_json_smoke(capsys):
    assert main(["diagnose", "-d", "0.4", "-i", "0.2", "--json"]) == 0
    rep = json.loads(capsys.readouterr().out)
    assert rep["findings"] and "summary" in rep


def test_cli_events_json(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr("wattson.watchdog.DEFAULT_LOG_PATH", tmp_path / "e.jsonl")
    import wattson.watchdog as wd

    monkeypatch.setattr(wd.Watchdog.__init__, "__defaults__", (tmp_path / "e.jsonl",))
    assert main(["events", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == []
