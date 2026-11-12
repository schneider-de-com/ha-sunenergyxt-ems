"""Forecasts must represent real, current, contiguous energy intervals."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.sunenergyxt_ems.forecast import ForecastError, power_forecast

NOW = datetime(2026, 11, 8, 12, tzinfo=UTC)


def rows():
    return [
        {"period_start": (NOW + timedelta(minutes=30 * i)).isoformat(), "pv_estimate": 2}
        for i in range(4)
    ]


def read(values=None, **kwargs):
    return power_forecast(
        rows() if values is None else values,
        now=NOW,
        issued_at=kwargs.pop("issued_at", NOW),
        unit=kwargs.pop("unit", "kW"),
        **kwargs,
    )


def test_power_is_integrated_and_current_partial_interval_is_clipped():
    forecast = power_forecast(rows(), now=NOW + timedelta(minutes=15), issued_at=NOW, unit="kW")
    assert forecast[0].start == NOW + timedelta(minutes=15)
    assert forecast[0].energy_wh == 500
    assert sum(slot.energy_wh for slot in forecast) == 3500


def test_watts_and_kw_are_equivalent():
    watts = [{**r, "pv_estimate": 2000} for r in rows()]
    assert read() == read(watts, unit="W")


@pytest.mark.parametrize("value", [None, True, -1, float("nan"), float("inf"), 200, 10**400])
def test_invalid_or_implausible_power_is_rejected(value):
    values = rows()
    values[1]["pv_estimate"] = value
    with pytest.raises(ForecastError):
        read(values)


@pytest.mark.parametrize("values", [[], rows()[1:], rows()[::2], rows() + rows(), rows()[::-1]])
def test_missing_overlapping_or_out_of_order_intervals_are_not_filled_with_zero(values):
    with pytest.raises(ForecastError):
        read(values)


@pytest.mark.parametrize("unit", ["%", "kWh", "EUR/kWh", None])
def test_unknown_power_units_fail_closed(unit):
    with pytest.raises(ForecastError):
        read(unit=unit)


@pytest.mark.parametrize("age", [timedelta(hours=25), timedelta(minutes=-6)])
def test_stale_or_future_issue_time_cannot_authorize_planning(age):
    with pytest.raises(ForecastError):
        read(issued_at=NOW - age)


def test_naive_time_and_short_horizon_are_rejected():
    values = rows()
    values[0]["period_start"] = "2026-11-08T12:00:00"
    with pytest.raises(ForecastError):
        read(values)
    with pytest.raises(ForecastError):
        read(required_until=NOW + timedelta(hours=3))


def test_two_days_join_without_double_counting_boundary():
    tomorrow = [
        {"period_start": (NOW + timedelta(minutes=30 * i)).isoformat(), "pv_estimate": 0}
        for i in range(4, 8)
    ]
    forecast = read(rows() + tomorrow, required_until=NOW + timedelta(hours=4))
    assert len(forecast) == 8
    assert sum(s.energy_wh for s in forecast) == 4000
