"""What the GPU is doing while it trains: clock, temperature, power, how
often it is held back by its power or temperature limit, and memory.

A background thread samples NVML once a second (the nvidia-ml-py package,
`pip install nvidia-ml-py`; without it only PyTorch's own memory figures
are reported). `take()` returns the averages since the last call, for the
training log: a run that slows down shows whether the clock fell (power or
heat) or the memory ran out (on Windows, CUDA then spills into system
memory instead of failing, and everything slows).
"""
from __future__ import annotations

import threading
import time

import torch

# NVML clock event ("throttle") reason bits.
POWER_CAP = 0x4
HW_SLOWDOWN = 0x8
SW_THERMAL = 0x20
HW_THERMAL = 0x40
POWER_BRAKE = 0x80


class GpuMonitor:
    def __init__(self, device, interval: float = 1.0):
        self.dev = torch.device(device)
        self.ok = self.dev.type == "cuda" and torch.cuda.is_available()
        self.samples: list[tuple] = []
        self.lock = threading.Lock()
        self.nvml = None
        if not self.ok:
            return
        try:
            import pynvml
            pynvml.nvmlInit()
            self.nvml = pynvml
            self.handle = pynvml.nvmlDeviceGetHandleByIndex(self.dev.index or torch.cuda.current_device())
            self.reasons = getattr(pynvml, "nvmlDeviceGetCurrentClocksEventReasons", None) or \
                getattr(pynvml, "nvmlDeviceGetCurrentClocksThrottleReasons", None)
        except Exception:
            self.nvml = None
            print("(pip install nvidia-ml-py to log the GPU's clock, temperature and power)", flush=True)
            return
        self.interval = interval
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        n = self.nvml
        failures = 0
        while failures < 600:
            try:
                s = (n.nvmlDeviceGetClockInfo(self.handle, n.NVML_CLOCK_SM),
                     n.nvmlDeviceGetTemperature(self.handle, n.NVML_TEMPERATURE_GPU),
                     n.nvmlDeviceGetPowerUsage(self.handle) / 1000.0,
                     n.nvmlDeviceGetUtilizationRates(self.handle).gpu,
                     self.reasons(self.handle) if self.reasons else 0,
                     n.nvmlDeviceGetMemoryInfo(self.handle).used / 2**30,
                     n.nvmlDeviceGetEnforcedPowerLimit(self.handle) / 1000.0)
                with self.lock:
                    self.samples.append(s)
                failures = 0
            except Exception:
                # A failed reading (the driver busy, the laptop switching
                # power modes) is skipped, not the end of sampling.
                failures += 1
            time.sleep(self.interval)

    def take(self) -> dict:
        """Averages since the last call (empty off CUDA)."""
        if not self.ok:
            return {}
        out = {"vram_peak_gb": torch.cuda.max_memory_reserved(self.dev) / 2**30}
        torch.cuda.reset_peak_memory_stats(self.dev)
        try:
            free, total = torch.cuda.mem_get_info(self.dev)
            out["vram_free_gb"] = free / 2**30
        except Exception:
            pass
        with self.lock:
            samples, self.samples = self.samples, []
        # Busy samples only: the clock drops while the GPU waits on the CPU.
        busy = [s for s in samples if s[3] >= 20] or samples
        if busy:
            k = len(busy)
            out.update({
                "gpu_mhz": sum(s[0] for s in busy) / k,
                "gpu_c": sum(s[1] for s in busy) / k,
                "gpu_w": sum(s[2] for s in busy) / k,
                "gpu_limit_w": busy[-1][6],
                "gpu_util": sum(s[3] for s in samples) / len(samples),
                "power_capped": sum(bool(s[4] & POWER_CAP) for s in busy) / k,
                "thermal": sum(bool(s[4] & (SW_THERMAL | HW_THERMAL | HW_SLOWDOWN)) for s in busy) / k,
                "vram_used_gb": max(s[5] for s in samples),
            })
        return out
