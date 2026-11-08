"""Learn demand from measured intervals without hiding insufficient history."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.sunenergyxt_ems.demand_forecast import demand_forecast
from custom_components.sunenergyxt_ems.forecast import EnergyInterval, ForecastError

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)
ZONE = ZoneInfo("Europe/Berlin")


def history(days=7, *, now=NOW):
    # Synthetic complete household measurements, 200 W day / 600 W night.
    start = now - timedelta(days=days)
    return [
        EnergyInterval(
            stamp := start + timedelta(minutes=30 * i),
            stamp + timedelta(minutes=30),
            100 if 7 <= stamp.astimezone(ZONE).hour < 22 else 300,
        )
        for i in range(days * 48)
    ]


def read(values=None, *, now=NOW, hours=24):
    return demand_forecast(
        history() if values is None else values,
        now=now,
        required_until=now + timedelta(hours=hours),
        timezone=ZONE,
    )


def test_learns_household_clock_profile_and_clips_current_slot():
    forecast = read(now=NOW + timedelta(minutes=15))
    assert forecast.expected[0].energy_wh == 50
    assert forecast.expected[0].start == NOW + timedelta(minutes=15)
    night = next(s for s in forecast.expected if s.start.astimezone(ZONE).hour == 23)
    assert night.energy_wh == 300
    assert forecast.expected[-1].end == NOW + timedelta(days=1, minutes=15)
    assert min(forecast.support_days) == 7
    assert forecast.history_end == NOW


def test_recent_behavior_has_greater_weight_than_old_behavior():
    values = history()
    values = [
        EnergyInterval(s.start, s.end, 100 if s.end <= NOW - timedelta(days=3) else 300)
        for s in values
    ]
    forecast = read(values, hours=1)
    assert 100 + 200 * 3 / 7 < forecast.expected[0].energy_wh < 300
    assert forecast.lower[0].energy_wh == 100
    assert forecast.upper[0].energy_wh == 300


@pytest.mark.parametrize("days", [0, 1, 2])
def test_fewer_than_three_distinct_days_cannot_authorize_forecast(days):
    with pytest.raises(ForecastError, match="insufficient_demand_history"):
        read(history(days))


def test_missing_clock_bucket_is_not_zero_or_daily_average():
    values = [s for s in history() if s.start.astimezone(ZONE).hour != 23]
    with pytest.raises(ForecastError, match="insufficient_demand_history"):
        read(values)


def test_old_history_does_not_authorize_current_planning():
    values = [
        EnergyInterval(s.start - timedelta(days=4), s.end - timedelta(days=4), s.energy_wh)
        for s in history()
    ]
    with pytest.raises(ForecastError, match="stale_demand_history"):
        read(values)


@pytest.mark.parametrize("energy", [-1, True, float("nan"), float("inf"), 10**400])
def test_invalid_household_energy_fails_closed(energy):
    values = history()
    values[-1] = EnergyInterval(values[-1].start, values[-1].end, energy)
    with pytest.raises(ForecastError):
        read(values)


def test_duplicate_overlapping_out_of_order_and_future_measurements_are_rejected():
    values = history()
    for invalid in [
        values + values[-1:],
        values[::-1],
        values + [EnergyInterval(NOW, NOW + timedelta(minutes=30), 100)],
    ]:
        with pytest.raises(ForecastError):
            read(invalid)


def test_partial_observed_slot_is_not_treated_as_complete_coverage():
    values = history()
    values[-1] = EnergyInterval(values[-1].start, values[-1].end - timedelta(minutes=1), 100)
    with pytest.raises(ForecastError, match="invalid_demand_interval"):
        read(values)


def test_many_fragments_from_one_day_do_not_count_as_three_days():
    values = history(1)
    with pytest.raises(ForecastError):
        read(values * 3)


def test_dst_repeated_hour_does_not_multiply_independent_support_days():
    # Three 02:00 samples on only two local days, including the repeated autumn hour.
    values = [
        EnergyInterval(start, start + timedelta(minutes=30), 100)
        for start in [
            datetime(2026, 10, 24, 0, tzinfo=UTC),
            datetime(2026, 10, 25, 0, tzinfo=UTC),
            datetime(2026, 10, 25, 1, tzinfo=UTC),
        ]
    ]
    with pytest.raises(ForecastError, match="insufficient_demand_history"):
        demand_forecast(
            values,
            now=datetime(2026, 10, 26, 1, tzinfo=UTC),
            required_until=datetime(2026, 10, 26, 1, 30, tzinfo=UTC),
            timezone=ZONE,
        )


def test_repeated_forecast_call_does_not_learn_from_its_own_predictions():
    values = history()
    assert read(values) == read(values)
    assert len(values) == 7 * 48


def test_fresh_other_clock_buckets_do_not_refresh_a_stale_night_bucket():
    values = [
        s
        for s in history()
        if s.start.astimezone(ZONE).hour != 23 or s.end <= NOW - timedelta(days=4)
    ]
    with pytest.raises(ForecastError, match="stale_demand_history"):
        read(values)


@pytest.mark.parametrize(
    "now", [datetime(2026, 3, 29, tzinfo=UTC), datetime(2026, 10, 25, tzinfo=UTC)]
)
def test_forecast_through_dst_transition_has_continuous_absolute_intervals(now):
    forecast = read(history(now=now), now=now, hours=8)
    assert len(forecast.expected) == 16
    assert forecast.expected[0].start == now
    assert forecast.expected[-1].end == now + timedelta(hours=8)
    assert all(
        left.end == right.start
        for left, right in zip(forecast.expected, forecast.expected[1:], strict=False)
    )


@pytest.mark.parametrize("hours", [0, -1, 49])
def test_invalid_forecast_horizon_is_rejected(hours):
    with pytest.raises(ForecastError, match="invalid_horizon"):
        read(hours=hours)
