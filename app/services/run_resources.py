"""Sample process CPU and RSS during one digest run; keep peaks only."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from time import perf_counter, process_time
from typing import Iterator

from app.models import CrawlRun

RssFn = Callable[[], int]
CpuFn = Callable[[], float]
WallFn = Callable[[], float]


@dataclass
class ResourcePeak:
    cpu_peak_percent: int = 0
    rss_peak_bytes: int = 0
    rss_delta_bytes: int = 0
    rss_baseline_bytes: int = 0


def current_rss_bytes() -> int:
    if sys.platform == "win32":
        return _win_working_set_bytes()
    try:
        with open("/proc/self/statm", encoding="utf-8") as fh:
            parts = fh.read().split()
        pages = int(parts[1])
        return pages * int(os.sysconf("SC_PAGE_SIZE"))
    except (OSError, IndexError, ValueError):
        return _rusage_rss_bytes()


def _rusage_rss_bytes() -> int:
    try:
        import resource
    except ImportError:
        return 0
    rss = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return rss
    return rss * 1024


def _win_working_set_bytes() -> int:
    import ctypes
    from ctypes import wintypes

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS)
    get_mem = ctypes.windll.psapi.GetProcessMemoryInfo
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not get_mem(handle, ctypes.byref(counters), counters.cb):
        return 0
    return int(counters.WorkingSetSize)


def apply_peak_to_run(row: CrawlRun, peak: ResourcePeak) -> None:
    row.cpu_peak_percent = max(int(row.cpu_peak_percent or 0), int(peak.cpu_peak_percent))
    row.rss_peak_bytes = max(int(row.rss_peak_bytes or 0), int(peak.rss_peak_bytes))
    row.rss_delta_bytes = max(int(row.rss_delta_bytes or 0), int(peak.rss_delta_bytes))


@contextmanager
def peak_sampler(
    *,
    interval: float = 0.1,
    rss_fn: RssFn | None = None,
    cpu_fn: CpuFn | None = None,
    wall_fn: WallFn | None = None,
) -> Iterator[ResourcePeak]:
    rss = rss_fn or current_rss_bytes
    cpu = cpu_fn or process_time
    wall = wall_fn or perf_counter
    peak = ResourcePeak()
    stop = threading.Event()
    lock = threading.Lock()
    prev_cpu = float(cpu())
    prev_wall = float(wall())
    first = True

    def snapshot() -> None:
        nonlocal prev_cpu, prev_wall, first
        rss_now = max(0, int(rss()))
        cpu_now = float(cpu())
        wall_now = float(wall())
        with lock:
            if first:
                peak.rss_baseline_bytes = rss_now
                first = False
            else:
                dt = wall_now - prev_wall
                if dt > 0:
                    pct = int(round((cpu_now - prev_cpu) / dt * 100))
                    peak.cpu_peak_percent = max(peak.cpu_peak_percent, max(0, pct))
            peak.rss_peak_bytes = max(peak.rss_peak_bytes, rss_now)
            peak.rss_delta_bytes = max(0, peak.rss_peak_bytes - peak.rss_baseline_bytes)
            prev_cpu = cpu_now
            prev_wall = wall_now

    snapshot()

    def loop() -> None:
        while not stop.wait(interval):
            snapshot()

    thread = threading.Thread(target=loop, name="todays3-peak-sampler", daemon=True)
    thread.start()
    try:
        yield peak
    finally:
        stop.set()
        thread.join(timeout=1.0)
        snapshot()
