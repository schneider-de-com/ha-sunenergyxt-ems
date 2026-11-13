"""Validate interval forecasts independently of HA and hardware control."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite


class ForecastError(ValueError):
    """The source cannot support a complete, current planning horizon."""


@dataclass(frozen=True, slots=True)
class EnergyInterval:
    """Energy in Wh over an absolute half-open time interval."""

    start: datetime
    end: datetime
    energy_wh: float


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ForecastError("timezone_required")
    return value.astimezone(UTC)


def period_energy_forecast(
    periods: Sequence[EnergyInterval], *, now: datetime, required_until: datetime
) -> tuple[EnergyInterval, ...]:
    """Resample documented period Wh onto half hours, conserving source energy.

    Uniform energy distribution *within* each supplied period is an explicit
    forecast model, not reconstructed measurement. Missing periods are errors.
    Long zero-energy periods must be supplied explicitly (e.g. source night).
    """
    now, until = _utc(now), _utc(required_until)
    if not timedelta() < until - now <= timedelta(days=2):
        raise ForecastError("invalid_horizon")
    checked: list[EnergyInterval] = []
    previous: datetime | None = None
    for period in periods:
        start, end, value = _utc(period.start), _utc(period.end), period.energy_wh
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(value)
            or not 0 <= value <= 100_000
            or not timedelta() < end - start <= timedelta(days=1)
            or (value > 0 and end - start > timedelta(hours=1))
        ):
            raise ForecastError("invalid_energy_period")
        if previous is not None and start != previous:
            raise ForecastError("noncontiguous_intervals")
        checked.append(EnergyInterval(start, end, float(value)))
        previous = end
    index = 0
    while index < len(checked) and checked[index].end <= now:
        index += 1
    result: list[EnergyInterval] = []
    cursor = now
    while cursor < until:
        boundary = cursor.replace(minute=cursor.minute // 30 * 30, second=0, microsecond=0)
        end = min(until, boundary + timedelta(minutes=30))
        position, energy = cursor, 0.0
        while position < end:
            if index >= len(checked) or not checked[index].start <= position < checked[index].end:
                raise ForecastError("incomplete_horizon")
            period = checked[index]
            stop = min(end, period.end)
            energy += period.energy_wh * ((stop - position) / (period.end - period.start))
            position = stop
            if position == period.end:
                index += 1
        result.append(EnergyInterval(cursor, end, energy))
        cursor = end
    return tuple(result)


def power_forecast(
    rows: Sequence[Mapping[str, object]],
    *,
    now: datetime,
    issued_at: datetime,
    unit: str,
    interval: timedelta = timedelta(minutes=30),
    required_until: datetime | None = None,
) -> tuple[EnergyInterval, ...]:
    """Integrate explicit W/kW means; reject gaps rather than inventing zeroes.

    The caller establishes the attribute's power unit from the provider
    contract. A parent sensor's daily kWh unit does not describe its nested
    power estimates. The default duration matches Solcast detailedForecast;
    another source must supply its documented interval duration explicitly.
    """
    now, issued_at = _utc(now), _utc(issued_at)
    until = _utc(required_until) if required_until is not None else None
    if unit not in ("W", "kW"):
        raise ForecastError("unsupported_power_unit")
    if not timedelta() < interval <= timedelta(hours=1) or (until is not None and until <= now):
        raise ForecastError("invalid_horizon")
    if not timedelta(minutes=-5) <= now - issued_at <= timedelta(hours=24):
        raise ForecastError("forecast_not_current")
    result: list[EnergyInterval] = []
    previous_end: datetime | None = None
    for row in rows:
        raw_start, raw_power = row.get("period_start"), row.get("pv_estimate")
        if not isinstance(raw_start, str):
            raise ForecastError("missing_interval_time")
        try:
            start = _utc(datetime.fromisoformat(raw_start))
        except ValueError as exc:
            raise ForecastError("invalid_interval_time") from exc
        if isinstance(raw_power, bool) or not isinstance(raw_power, (int, float)):
            raise ForecastError("invalid_power")
        if not 0 <= raw_power <= (100 if unit == "kW" else 100_000):
            raise ForecastError("invalid_power")
        watts = float(raw_power) * (1000 if unit == "kW" else 1)
        if not isfinite(watts) or not 0 <= watts <= 100_000:
            raise ForecastError("invalid_power")
        if previous_end is not None and start != previous_end:
            raise ForecastError("noncontiguous_intervals")
        end = start + interval
        previous_end = end
        clipped_start = max(start, now)
        clipped_end = min(end, until) if until is not None else end
        if clipped_end > clipped_start:
            if not result and clipped_start != now:
                raise ForecastError("current_interval_missing")
            result.append(
                EnergyInterval(
                    clipped_start,
                    clipped_end,
                    watts * (clipped_end - clipped_start).total_seconds() / 3600,
                )
            )
    if not result or result[-1].end < (until or now + timedelta(hours=1)):
        raise ForecastError("incomplete_horizon")
    return tuple(result)
