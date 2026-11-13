"""Measured power integration must retain coverage, outages and restart gaps."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.sunenergyxt_ems.forecast import ForecastError
from custom_components.sunenergyxt_ems.operating_history import OperatingHistory, OperatingSample

NOW = datetime(2026, 11, 12, 12, tzinfo=UTC)


def sample(seconds, grid=3600, dc=None, state="running"):
    return OperatingSample(NOW + timedelta(seconds=seconds), {"grid": grid, "dc:store": dc}, state)


def test_signed_import_export_are_separate_and_unknown_dc_is_not_zero():
    history = OperatingHistory()
    history.observe(sample(0))
    history.observe(sample(5))
    history.observe(sample(10, -1800))
    history.observe(sample(15, -1800))
    row = history.rows[0]
    assert row["channels"]["grid"]["positive_wh"] == 10
    assert row["channels"]["grid"]["negative_wh"] == 2.5
    assert row["channels"]["grid"]["covered_seconds"] == 15
    assert "dc:store" not in row["channels"]


def test_partial_channel_outage_never_poison_or_fill_other_channels():
    history = OperatingHistory()
    for s, grid in ((0, 3600), (5, 3600), (10, None), (15, None), (20, 3600), (25, 3600)):
        history.observe(sample(s, grid, dc=720))
    channels = history.rows[0]["channels"]
    assert channels["grid"]["covered_seconds"] == 10
    assert channels["grid"]["positive_wh"] == 10
    assert channels["dc:store"]["covered_seconds"] == 25


def test_restore_retains_values_and_marks_downtime_without_energy():
    history = OperatingHistory()
    history.observe(sample(0))
    history.observe(sample(5))
    saved = history.as_dict()
    restored = OperatingHistory.from_dict(saved, now=NOW + timedelta(minutes=3))
    restored.observe(sample(180))
    assert sum(row["channels"].get("grid", {}).get("positive_wh", 0) for row in restored.rows) == 5
    assert sum(row["states"].get("observer_gap", 0) for row in restored.rows) == 175
    restored.observe(sample(185))
    assert restored.rows[-1]["channels"]["grid"]["positive_wh"] == 5


def test_minute_boundaries_and_controller_inactivity_are_explicit():
    history = OperatingHistory()
    history.observe(sample(55, state="reserve_safety"))
    history.observe(sample(65, state="disabled"))
    assert len(history.rows) == 2
    assert [row["elapsed_seconds"] for row in history.rows] == [5, 5]
    assert all(row["states"] == {"reserve_safety": 5} for row in history.rows)


def test_clock_reversal_and_invalid_power_are_rejected_without_changing_evidence():
    history = OperatingHistory()
    history.observe(sample(10))
    for invalid in (sample(5), sample(15, float("nan")), sample(15, True)):
        before = history.as_dict()
        with pytest.raises(ForecastError):
            history.observe(invalid)
        assert history.as_dict() == before


def test_unreadable_or_future_store_is_rejected():
    history = OperatingHistory()
    history.observe(sample(0))
    history.observe(sample(5))
    saved = history.as_dict()
    saved["rows"][0]["channels"]["grid"]["covered_seconds"] = 100
    with pytest.raises(ForecastError):
        OperatingHistory.from_dict(saved, now=NOW + timedelta(seconds=5))


def test_long_gaps_are_retained_only_in_the_bounded_window():
    history = OperatingHistory()
    history.observe(sample(0))
    history.observe(sample(30 * 86400))
    assert len(history.rows) <= 14 * 24 * 60 + 1
    assert all(not row["channels"] for row in history.rows)


def test_fractional_time_maximum_power_roundtrips_its_own_checkpoint():
    history = OperatingHistory()
    for i in range(100):
        history.observe(
            OperatingSample(
                NOW + timedelta(microseconds=123456 + i * 5101234), {"grid": 100_000}, "running"
            )
        )
    saved = history.as_dict()
    restored = OperatingHistory.from_dict(saved, now=NOW + timedelta(minutes=10))
    assert restored.as_dict() == saved


def test_internal_summary_reports_all_retained_coverage_beyond_recent_preview():
    history = OperatingHistory()
    for seconds in range(0, 601, 5):
        history.observe(sample(seconds, grid=3600 if seconds <= 30 else None, dc=720))
    report = history.summary()
    assert len(report["recent_rows"]) == 5
    assert report["retained_elapsed_seconds"] == 600
    assert report["channels"]["grid"]["positive_wh"] == 30
    assert report["channels"]["grid"]["covered_seconds"] == 30
    assert report["channels"]["dc:store"]["covered_seconds"] == 600
    assert report["states"] == {"running": 600}
