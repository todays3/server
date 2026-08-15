"""Peak CPU/RAM for a single digest run — no time series."""

from __future__ import annotations

import time

from app.models import CrawlRun
from app.services.run_resources import ResourcePeak, apply_peak_to_run, current_rss_bytes, peak_sampler


def test_peak_sampler_tracks_rss_max_and_delta():
    values = iter([100, 250, 180, 180, 180, 180, 180])

    def rss() -> int:
        try:
            return next(values)
        except StopIteration:
            return 180

    with peak_sampler(interval=0.01, rss_fn=rss) as peak:
        time.sleep(0.08)

    assert peak.rss_baseline_bytes == 100
    assert peak.rss_peak_bytes == 250
    assert peak.rss_delta_bytes == 150
    assert peak.cpu_peak_percent >= 0


def test_apply_peak_to_run_keeps_maxima():
    row = CrawlRun(
        user_id=1,
        trigger="schedule",
        cpu_peak_percent=10,
        rss_peak_bytes=200,
        rss_delta_bytes=50,
    )
    apply_peak_to_run(row, ResourcePeak(cpu_peak_percent=40, rss_peak_bytes=180, rss_delta_bytes=90))
    assert row.cpu_peak_percent == 40
    assert row.rss_peak_bytes == 200
    assert row.rss_delta_bytes == 90
    apply_peak_to_run(row, ResourcePeak(cpu_peak_percent=12, rss_peak_bytes=400, rss_delta_bytes=20))
    assert row.cpu_peak_percent == 40
    assert row.rss_peak_bytes == 400
    assert row.rss_delta_bytes == 90


def test_current_rss_bytes_is_non_negative():
    assert current_rss_bytes() >= 0
