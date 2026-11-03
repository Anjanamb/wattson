# wattson diagnose: finding reference

One section per finding `id` (schema version 1). Thresholds come from
`src/wattson/diagnose.py` and `src/wattson/watchdog.py`.

Each entry has: **Trigger** (what the rule checks) · **Read the evidence** ·
**Look in code** · **False alarm when…**

---

## gpu_starved_single_thread  (warn)

**Trigger:** the GPU is held by a process, its average util is < 60%, and the
hottest CPU core averages ≥ 90% while the whole CPU averages < 50%.

**Read the evidence:** `hottest_core_avg_pct` ≈ 100 and a low `cpu_avg_pct`
together mean one Python thread is the bottleneck. In `gpu_processes`, the
training process itself shows `cpu_pct` ≈ 100 (one full core).

**Look in code:**
- `DataLoader(..., num_workers=0)`, or `num_workers` missing (default 0). In
  HF `TrainingArguments`, `dataloader_num_workers` defaults to 0.
- Heavy per-sample Python (`PIL` decode, tokenization, augmentation) inside
  the training loop instead of in `Dataset.__getitem__`.
- On Windows/macOS, workers silently disabled because the script has no
  `if __name__ == "__main__":` guard.

**Fix:** `num_workers` ≈ physical cores − 1 (not logical cores; check
`summary.cpu.logical_cores` and halve it if SMT is on), `pin_memory=True`,
`persistent_workers=True`.

**False alarm when:** the model is genuinely tiny and Python overhead per step
dominates. Then the fix is a bigger batch or `torch.compile`, not workers.

## gpu_starved_cpu  (warn)

**Trigger:** GPU util < 60% while it holds a job, and CPU average ≥ 85%.

**Read the evidence:** all cores are busy. Check `summary.processes`: are the
busy processes DataLoader workers (same cmdline as the trainer) or something
unrelated (browser, indexer, another job)?

**Look in code:** expensive augmentation (big resizes, random crops on
full-resolution images), on-the-fly tokenization, JSON/CSV parsing per
sample, `num_workers` > physical cores.

**Fix:** pre-process once and cache; move augmentation to the GPU
(torchvision v2 on CUDA tensors, NVIDIA DALI, Kornia); if an unrelated
process is eating the CPU, tell the user what it is.

## gpu_starved_io  (warn)

**Trigger:** GPU util < 60% while it holds a job, and iowait ≥ 10% or disk
reads ≥ 150 MB/s.

**Read the evidence:** `iowait_pct` is Linux-only (null on Windows).
`disk_read_mb_s` near the drive's limit (SATA SSD ~500, HDD ~150, network
mount often far lower) confirms it.

**Look in code / setup:** dataset path on a network share, USB disk or HDD;
in WSL2, data under `/mnt/c/...` (very slow 9P bridge). Look for millions of
small files (one JPEG per sample).

**Fix:** copy the data to local NVMe (inside WSL: under `~`, not `/mnt/c`);
shard small files (WebDataset/LMDB/TFRecord); raise `prefetch_factor`.

## gpu_underutilized  (info)

**Trigger:** GPU util < 60% while it holds a job, with no CPU or I/O
bottleneck.

**Read the evidence:** a large `util_max − util_min` spread means bursty work
(sync points, eval, checkpointing). A flat low util means too little work per
step.

**Look in code:** `loss.item()` / `print` / metric `.cpu()` every step; small
`batch_size`; no mixed precision; `torch.cuda.synchronize()` in the loop;
`find_unused_parameters=True` in DDP.

**Fix:** log every N steps; larger batch or gradient accumulation;
`torch.autocast`; `torch.compile`. Re-measure mid-epoch to rule out an
eval/checkpoint phase.

## stale_gpu_process  (warn)

**Trigger:** GPU util < 5% while a process holds VRAM and uses < 1% CPU.

**Read the evidence:** `processes[].cmdline` usually names it:
`ipykernel_launcher` (an open notebook), an old `train.py`, a `tensorboard`
or a hung `pt_main_thread` DataLoader worker.

**Fix:** tell the user what it is. For notebooks, restart the kernel. Offer
`wattson kill <pid>` (listed in `actions`) **only with explicit approval**.

**False alarm when:** the user's job is between phases (loading data,
waiting on a lock, in the middle of saving). Re-measure before suggesting a
kill.

## gpu_thermal_throttle  (warn / crit ≥ 95 °C)

**Trigger:** thermal or HW-slowdown throttle reasons are active in ≥ 20% of
samples, or the GPU max temperature is ≥ 85 °C.

**Fix:** airflow, cleaning, a laptop stand, fan curve. Lowering the power
limit 10–20% (`wattson power <watts>`, needs admin, ask first) often costs
< 5% speed and removes the throttling entirely.

## gpu_power_capped  (info)

**Trigger:** power-cap throttling in ≥ 20% of samples while the GPU is busy
(util ≥ 60%).

**Read the evidence:** this is normal at full load. It only matters if
`power_cap_w` is much lower than the card's rating. On laptops the usual
cause is running on battery or a "quiet"/"balanced" power profile.

## vram_near_full  (warn ≥ 90% / crit ≥ 98%)

**Read the evidence:** PyTorch's caching allocator holds freed memory, so a
high reading can be benign. It is a real risk if the job has already OOM'd,
sequence lengths vary, or several `holders` share the GPU.

**Fix:** bf16/fp16, gradient checkpointing, a smaller micro-batch with
gradient accumulation, `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True`,
closing other GPU processes.

## gpu_contention  (info)

**Trigger:** two or more processes hold VRAM on the same GPU.

**Fix:** confirm each one is intended. DDP/torchrun workers on *different*
GPUs are fine; two trainers on one GPU will slow each other.

## ram_pressure  (warn ≥ 90% / crit ≥ 95%)

**Look in code:** Dataset objects that hold big Python lists/dicts (copied
into every worker through copy-on-access refcounts), `pandas` frames inside
the Dataset, loading the whole dataset into memory, a large `prefetch_factor
× num_workers × batch`.

**Fix:** numpy arrays or memmap instead of Python objects; lazy loading;
fewer workers.

## swapping  (warn)

**Trigger:** swap in+out ≥ 1 MB/s during the window. Usually comes with
`ram_pressure`. Fix the memory use; everything is slower while swapping.

## disk_nearly_full  (warn ≥ 90% / crit ≥ 97%)

**Fix:** checkpoint retention (`save_total_limit`, keep best/last only);
clean `~/.cache/huggingface`, `~/.cache/pip`, `conda clean -a`, old
`wandb/` and `lightning_logs/` runs. Ask before deleting anything.

## cpu_hot  (warn ≥ 90 °C / crit ≥ 100 °C)

Common on laptops under sustained single-core load. The CPU will throttle,
which slows data loading. Fix with cooling or a lower load (fewer workers,
if the CPU is not the bottleneck anyway).

## no_gpu  (info)

NVML is unavailable, so GPU checks were skipped. Check that `nvidia-smi`
works in the same shell. In Docker use `--gpus all`. In WSL2 the driver
comes from Windows. AMD and Apple GPUs are not supported yet.

## healthy  (info)

Nothing crossed a warning threshold. If the user still sees slowness, it may
be intermittent: measure during the slow phase, use a longer `--duration`,
or check `wattson events --json`.
