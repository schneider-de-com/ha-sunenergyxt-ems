"""Solcast source metadata and uncertainty must survive the HA boundary."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

pytest.importorskip("homeassistant")
from homeassistant.core import State  # noqa: E402

from custom_components.sunenergyxt_ems.forecast import ForecastError  # noqa: E402
from custom_components.sunenergyxt_ems.forecast_sources import solcast_forecast  # noqa: E402

NOW = datetime(2026, 11, 8, 22, 45, tzinfo=UTC)


def day(start, *, updated=NOW, **changes):
    attributes = {
        "unit_of_measurement": "kWh",
        "dataCorrect": True,
        "detailedForecast": [
            {
                "period_start": start + timedelta(minutes=30 * i),
                "pv_estimate": 2,
                "pv_estimate10": 1,
                "pv_estimate90": 3,
            }
            for i in range(48)
        ],
    }
    attributes.update(changes)
    return State("sensor.solcast_day", "48", attributes, last_updated=updated)


def sources():
    midnight = datetime(2026, 11, 8, 23, tzinfo=UTC)
    return [day(midnight - timedelta(days=1)), day(midnight)]


def issued(value=NOW):
    return State("sensor.solcast_last_success", value.isoformat())


def read(days=None, last_success=None, **kwargs):
    return solcast_forecast(
        sources() if days is None else days,
        issued() if last_success is None else last_success,
        now=NOW,
        required_until=kwargs.pop("required_until", NOW + timedelta(hours=10)),
        **kwargs,
    )


def test_joins_native_ha_datetimes_across_midnight_with_explicit_kw_contract():
    forecast = read()
    assert forecast.expected[0].energy_wh == 500  # Parent kWh is NOT nested power unit.
    assert sum(s.energy_wh for s in forecast.expected) == 20_000
    assert sum(s.energy_wh for s in forecast.lower) == 10_000
    assert sum(s.energy_wh for s in forecast.upper) == 30_000
    assert forecast.expected[-1].end == NOW + timedelta(hours=10)
    assert forecast.issued_at == NOW


def test_serialized_timestamps_have_identical_energy():
    days = sources()
    for state in days:
        for row in state.attributes["detailedForecast"]:
            row["period_start"] = row["period_start"].isoformat()
    assert read(days).expected == read().expected


@pytest.mark.parametrize(
    "state", [None, State("sensor.day", "unavailable"), State("sensor.day", "unknown")]
)
def test_missing_or_unavailable_day_never_becomes_zero_pv(state):
    with pytest.raises(ForecastError):
        read([sources()[0], state])


@pytest.mark.parametrize(
    "changes",
    [
        {"dataCorrect": False},
        {"dataCorrect": None},
        {"unit_of_measurement": "W"},
        {"detailedForecast": None},
        {"detailedForecast": "not intervals"},
        {"detailedForecast": [None]},
        {"detailedForecast": []},
    ],
)
def test_missing_or_invalid_source_contract_is_rejected(changes):
    days = sources()
    days[1] = day(datetime(2026, 11, 8, 23, tzinfo=UTC), **changes)
    with pytest.raises(ForecastError):
        read(days)


@pytest.mark.parametrize("value", ["unavailable", "unknown", "bad time", "2026-11-08T22:00:00"])
def test_last_api_success_is_required_not_the_parent_report_time(value):
    with pytest.raises(ForecastError):
        read(last_success=State("sensor.last_success", value))


def test_missing_api_success_cannot_authorize_planning():
    with pytest.raises(ForecastError):
        solcast_forecast(sources(), None, now=NOW, required_until=NOW + timedelta(hours=10))


def test_old_api_success_is_not_refreshed_by_new_sensor_reports():
    with pytest.raises(ForecastError, match="forecast_not_current"):
        read(last_success=issued(NOW - timedelta(hours=25)))


def test_new_api_success_does_not_certify_a_stale_parent():
    days = sources()
    days[1] = day(datetime(2026, 11, 8, 23, tzinfo=UTC), updated=NOW - timedelta(hours=25))
    with pytest.raises(ForecastError, match="forecast_not_current"):
        read(days)


@pytest.mark.parametrize(
    "change",
    [{"pv_estimate10": None}, {"pv_estimate90": -1}, {"pv_estimate10": 4}, {"pv_estimate90": 1}],
)
def test_uncertainty_is_required_and_ordered(change):
    days = sources()
    days[1].attributes["detailedForecast"][0].update(change)
    with pytest.raises(ForecastError):
        read(days)


def test_missing_midnight_interval_is_not_interpolated():
    days = sources()
    days[1].attributes["detailedForecast"].pop(0)
    with pytest.raises(ForecastError, match="noncontiguous_intervals"):
        read(days)


def test_dst_repeated_hour_is_absolute_time_not_duplicate_local_time():
    start = datetime(2026, 10, 25, tzinfo=ZoneInfo("Europe/Berlin"))
    absolute = start.astimezone(UTC)
    state = day(absolute, updated=absolute)
    for row in state.attributes["detailedForecast"]:
        row["period_start"] = row["period_start"].astimezone(start.tzinfo)
    forecast = solcast_forecast(
        [state], issued(absolute), now=absolute, required_until=absolute + timedelta(hours=5)
    )
    assert sum(slot.energy_wh for slot in forecast.expected) == 10_000


def test_night_zeroes_are_source_intervals_not_a_filled_gap():
    days = sources()
    for row in days[0].attributes["detailedForecast"]:
        row.update(pv_estimate=0, pv_estimate10=0, pv_estimate90=0)
    forecast = read(days)
    assert forecast.expected[0].energy_wh == 0
    assert sum(slot.energy_wh for slot in forecast.expected) == 19_500
    days[0].attributes["detailedForecast"].pop()
    with pytest.raises(ForecastError, match="noncontiguous_intervals"):
        read(days)


def test_input_states_are_not_mutated():
    days = sources()
    before = repr(days)
    read(days)
    assert repr(days) == before
