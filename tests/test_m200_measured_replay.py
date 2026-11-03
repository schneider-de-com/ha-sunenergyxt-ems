"""Deterministic controller replay of the real 2026-11-03 M200 trace.

The comparison shares exactly the observed grid values and reported M200 limits.
It counts proposed zero commands. It must NOT claim the counterfactual physical
grid trace would be identical after changing the controller.
"""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.sunenergyxt_ems.logic import (
    Battery,
    Controller,
    GridMedianFilter,
    Inverter,
)

FIXTURE = Path(__file__).parent / "fixtures" / "m200_grid_startup_20261103.json"


def replay(*, filtered: bool) -> tuple[int, int]:
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    grid = raw["series"]["grid_w"]
    limits = raw["series"]["m200_limit_pct"]
    assert len(grid) >= 200 and len(limits) >= 60
    assert raw["series"]["phase_a_w"] and raw["series"]["bk215_dc_output_w"]
    assert raw["series"]["m200_ac_sample_w"]
    controller = Controller()
    battery = Battery("bk1", 89, 10, 100, (Inverter("m200", 2000),))
    meter_filter = GridMedianFilter()
    index = 0
    zero_requests = 0
    all_requests = 0
    for time_s, grid_w in grid:
        while index + 1 < len(limits) and limits[index + 1][0] <= time_s:
            index += 1
        reported_pct = limits[index][1] if limits[index][0] <= time_s else 0
        # At each reading use exactly the *actual recorded actuator* as baseline.
        controller.state.outputs = {"m200": reported_pct * 20.0}
        value = meter_filter.observe(grid_w, time_s) if filtered else grid_w
        proposed = controller.update((battery,), value, time_s + 1, sequence=time_s)["m200"]
        if reported_pct > 0 and proposed <= 0.0:
            zero_requests += 1
        if abs(proposed - reported_pct * 20.0) >= 10.0:
            all_requests += 1
    return zero_requests, all_requests


def test_real_recorder_replay_reduces_startup_zero_hunting() -> None:
    baseline_zero, baseline_changes = replay(filtered=False)
    new_zero, new_changes = replay(filtered=True)
    assert baseline_zero >= 18, "Replay must reproduce the observed zero hunting"
    assert new_zero <= 2, "Short transients must not restart the M200"
    assert new_changes < baseline_changes


def test_distinct_samples_only_and_real_persistent_export() -> None:
    meter = GridMedianFilter()
    assert meter.observe(20.0, "a") == 20.0
    for _ in range(12):
        assert meter.observe(-700.0, "b") == -340.0
    # Repeated scheduler ticks are not new meter observations.
    assert meter.observe(20.0, "c") == 20.0
    assert meter.observe(-700.0, "d") == -340.0
    assert meter.observe(-710.0, "e") == -700.0
    assert meter.observe(-720.0, "f") == -700.0


def test_reset_discards_stale_pre_shutdown_grid_samples() -> None:
    meter = GridMedianFilter()
    for i in range(5):
        meter.observe(420.0, i)
    meter.reset()
    assert meter.observe(-400.0, "new-session") == -400.0


def test_invalid_meter_does_not_get_masked_by_historical_median() -> None:
    meter = GridMedianFilter()
    for i in range(5):
        meter.observe(400.0, i)
    assert meter.observe(float("nan"), 5) != meter.observe(float("nan"), 5)
    assert meter.observe(1_000_000.0, 6) == 1_000_000.0
    assert meter.observe(-100.0, 7) == -100.0
