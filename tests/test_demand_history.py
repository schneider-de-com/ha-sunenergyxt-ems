"""Only continuously observed household intervals may enter learning history."""

import json
from datetime import UTC, datetime, timedelta

import pytest

from custom_components.sunenergyxt_ems.demand_history import DemandHistory
from custom_components.sunenergyxt_ems.forecast import ForecastError

START = datetime(2026, 11, 8, 12, tzinfo=UTC)


def record(history, start=START, *, minutes=30, watts=600):
    for seconds in range(0, minutes * 60 + 1, 30):
        history.observe(start + timedelta(seconds=seconds), watts)


def test_continuous_observations_produce_complete_measured_energy():
    history = DemandHistory()
    record(history)
    assert len(history.intervals) == 1
    assert history.intervals[0].energy_wh == pytest.approx(300)
    assert history.intervals[0].start == START
    assert history.intervals[0].end == START + timedelta(minutes=30)


def test_starting_between_boundaries_does_not_invent_missing_prefix():
    history = DemandHistory()
    record(history, START + timedelta(minutes=5), minutes=60)
    assert len(history.intervals) == 1
    assert history.intervals[0].start == START + timedelta(minutes=30)
    assert history.intervals[0].energy_wh == pytest.approx(300)


def test_changed_load_is_integrated_not_replaced_by_latest_value():
    history = DemandHistory()
    record(history, minutes=15, watts=600)
    record(history, START + timedelta(minutes=15, seconds=30), minutes=15, watts=1200)
    assert history.intervals[0].energy_wh == pytest.approx(445)


@pytest.mark.parametrize("missing", [True, False])
def test_missing_value_or_long_silence_discards_affected_slot(missing):
    history = DemandHistory()
    record(history, minutes=10)
    if missing:
        history.observe(START + timedelta(minutes=11), None)
    record(history, START + timedelta(minutes=20), minutes=40)
    assert len(history.intervals) == 1
    assert history.intervals[0].start == START + timedelta(minutes=30)


def test_duplicate_sample_time_does_not_add_coverage():
    history = DemandHistory()
    for _ in range(100):
        history.observe(START, 600)
    assert history.intervals == ()
    history.observe(START + timedelta(minutes=30), 600)
    assert history.intervals == ()


def test_restart_preserves_complete_intervals_but_never_bridges_downtime():
    history = DemandHistory()
    record(history, minutes=45)
    saved = json.loads(json.dumps(history.as_dict()))
    restored = DemandHistory.from_dict(saved, now=START + timedelta(minutes=50))
    record(restored, START + timedelta(minutes=50), minutes=40)
    assert len(restored.intervals) == 2
    assert restored.intervals[0].start == START
    assert restored.intervals[1].start == START + timedelta(hours=1)


@pytest.mark.parametrize("value", [-1, True, float("nan"), float("inf"), 10**400])
def test_invalid_power_breaks_coverage_and_fails_closed(value):
    history = DemandHistory()
    record(history, minutes=10)
    with pytest.raises(ForecastError):
        history.observe(START + timedelta(minutes=11), value)
    record(history, START + timedelta(minutes=12), minutes=18)
    assert history.intervals == ()


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": 99, "intervals": []},
        {"version": 1, "intervals": [None]},
        {"version": 1, "intervals": "bad"},
    ],
)
def test_corrupt_saved_state_does_not_create_measurements(payload):
    with pytest.raises(ForecastError):
        DemandHistory.from_dict(payload, now=START)


def test_restore_rejects_future_or_duplicate_interval():
    history = DemandHistory()
    record(history)
    saved = history.as_dict()
    with pytest.raises(ForecastError):
        DemandHistory.from_dict(saved, now=START)
    saved["intervals"] *= 2
    with pytest.raises(ForecastError):
        DemandHistory.from_dict(saved, now=START + timedelta(hours=1))


def test_old_intervals_expire_after_twenty_eight_days():
    history = DemandHistory()
    record(history)
    record(history, START + timedelta(days=29))
    assert len(history.intervals) == 1
    assert history.intervals[0].start == START + timedelta(days=29)


def test_maximum_valid_power_survives_serialization_without_overflow():
    history = DemandHistory()
    record(history, watts=100_000)
    restored = DemandHistory.from_dict(history.as_dict(), now=START + timedelta(hours=1))
    assert restored.intervals[0].energy_wh == pytest.approx(50_000)


def test_out_of_order_sample_breaks_partial_coverage():
    history = DemandHistory()
    record(history, minutes=10)
    with pytest.raises(ForecastError, match="unordered_demand_sample"):
        history.observe(START + timedelta(minutes=9), 600)
    record(history, START + timedelta(minutes=11), minutes=19)
    assert history.intervals == ()


def test_conflicting_duplicate_is_not_an_independent_observation():
    history = DemandHistory()
    record(history, minutes=10)
    with pytest.raises(ForecastError, match="unordered_demand_sample"):
        history.observe(START + timedelta(minutes=10), 1200)
    record(history, START + timedelta(minutes=11), minutes=19)
    assert history.intervals == ()


def test_fractional_second_maximum_power_round_trip_remains_valid():
    history = DemandHistory()
    for micros in range(0, 1_800_000_000, 33_333_333):
        history.observe(START + timedelta(microseconds=micros), 100_000)
    boundary = START + timedelta(minutes=30)
    history.observe(boundary, 100_000)
    restored = DemandHistory.from_dict(history.as_dict(), now=boundary + timedelta(minutes=30))
    assert restored.intervals[0].energy_wh == 50_000
