"""Source energy budgets survive resampling without inventing missing periods."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.sunenergyxt_ems.forecast import (
    EnergyInterval,
    ForecastError,
    period_energy_forecast,
)

START = datetime(2026, 11, 13, 12, tzinfo=UTC)


def test_hourly_wh_are_energy_not_watts_or_half_hour_energy():
    rows = [EnergyInterval(START, START + timedelta(hours=1), 200)]
    result = period_energy_forecast(rows, now=START, required_until=rows[-1].end)
    assert [row.energy_wh for row in result] == [100, 100]
    assert result[0].start == START
    assert result[-1].end == rows[-1].end


def test_partial_source_periods_join_the_demand_half_hour_grid():
    # The zero before sunrise and the following production share a half hour.
    sunrise = START + timedelta(minutes=10)
    rows = [
        EnergyInterval(START - timedelta(hours=12), sunrise, 0),
        EnergyInterval(sunrise, START + timedelta(hours=1), 100),
    ]
    result = period_energy_forecast(rows, now=START, required_until=rows[-1].end)
    assert [(r.start, r.end) for r in result] == [
        (START, START + timedelta(minutes=30)),
        (START + timedelta(minutes=30), START + timedelta(hours=1)),
    ]
    assert [r.energy_wh for r in result] == [40, 60]


def test_clipping_preserves_uniform_within_period_model_not_observed_shape():
    rows = [EnergyInterval(START, START + timedelta(hours=1), 200)]
    result = period_energy_forecast(
        rows, now=START + timedelta(minutes=15), required_until=START + timedelta(minutes=45)
    )
    assert [r.energy_wh for r in result] == [50, 50]


def test_explicit_source_night_zero_is_usable_but_an_absent_interval_is_not():
    end = START + timedelta(hours=15, minutes=7)
    rows = [EnergyInterval(START, end, 0)]
    result = period_energy_forecast(rows, now=START, required_until=end)
    assert result and sum(r.energy_wh for r in result) == 0
    assert result[-1].end == end
    with pytest.raises(ForecastError):
        period_energy_forecast([], now=START, required_until=end)


@pytest.mark.parametrize("value", [True, -1, float("nan"), float("inf"), "100"])
def test_invalid_energy_is_not_coerced_into_an_estimate(value):
    with pytest.raises(ForecastError):
        period_energy_forecast(
            [EnergyInterval(START, START + timedelta(hours=1), value)],
            now=START,
            required_until=START + timedelta(hours=1),
        )


@pytest.mark.parametrize(
    "rows",
    [
        [EnergyInterval(START + timedelta(minutes=1), START + timedelta(hours=1), 100)],
        [EnergyInterval(START, START + timedelta(minutes=59), 100)],
        [EnergyInterval(START, START + timedelta(hours=2), 100)],
        [EnergyInterval(START, START, 0)],
        [
            EnergyInterval(START, START + timedelta(minutes=20), 50),
            EnergyInterval(START + timedelta(minutes=21), START + timedelta(hours=1), 50),
        ],
        [
            EnergyInterval(START, START + timedelta(minutes=40), 50),
            EnergyInterval(START + timedelta(minutes=30), START + timedelta(hours=1), 50),
        ],
    ],
)
def test_missing_overlapping_or_long_nonzero_periods_fail_closed(rows):
    with pytest.raises(ForecastError):
        period_energy_forecast(rows, now=START, required_until=START + timedelta(hours=1))


def test_dst_repeated_local_hour_uses_absolute_intervals():
    start = datetime.fromisoformat("2026-10-25T02:00:00+02:00")
    end = datetime.fromisoformat("2026-10-25T02:00:00+01:00")
    result = period_energy_forecast(
        [EnergyInterval(start, end, 200)], now=start, required_until=end
    )
    assert len(result) == 2
    assert sum(r.energy_wh for r in result) == 200
