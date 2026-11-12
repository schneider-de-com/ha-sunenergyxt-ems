"""Read existing HA forecast states without contacting hardware or providers."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from math import isfinite

from homeassistant.core import State

from .forecast import EnergyInterval, ForecastError, power_forecast


@dataclass(frozen=True, slots=True)
class SolarForecast:
    """Provider estimates, not measured energy or guaranteed production."""

    expected: tuple[EnergyInterval, ...]
    lower: tuple[EnergyInterval, ...]
    upper: tuple[EnergyInterval, ...]
    issued_at: datetime
    source_updated_at: datetime
    source_entities: tuple[str, ...]


def _timestamp(value: object) -> datetime:
    try:
        stamp = datetime.fromisoformat(value) if isinstance(value, str) else value
    except ValueError as exc:
        raise ForecastError("invalid_source_time") from exc
    if not isinstance(stamp, datetime) or stamp.tzinfo is None or stamp.utcoffset() is None:
        raise ForecastError("invalid_source_time")
    return stamp.astimezone(UTC)


def solcast_forecast(
    days: Sequence[State | None],
    last_success: State | None,
    *,
    now: datetime,
    required_until: datetime,
) -> SolarForecast:
    """Require complete, current Solcast intervals including uncertainty."""
    # Solcast detailedForecast uses half-hour mean kW, independently of the
    # daily parent sensor's kWh. Contract verified against BJReplay/
    # ha-solcast-solar v4.6.4 (const.py and solcastapi.forecast_entry_update).
    # Read only: the caller supplies states from a verified Solcast config entry.
    now = _timestamp(now)
    if last_success is None:
        raise ForecastError("missing_api_success")
    issued_at = _timestamp(last_success.state)
    updated: list[datetime] = []
    entities: list[str] = [last_success.entity_id]
    rows: list[Mapping[str, object]] = []
    for state in days:
        if state is None:
            raise ForecastError("missing_forecast_source")
        try:
            total = float(state.state)
        except ValueError as exc:
            raise ForecastError("unavailable_forecast_source") from exc
        if not isfinite(total) or total < 0:
            raise ForecastError("unavailable_forecast_source")
        if (
            state.attributes.get("unit_of_measurement") != "kWh"
            or state.attributes.get("dataCorrect") is not True
        ):
            raise ForecastError("invalid_solcast_contract")
        stamp = _timestamp(state.last_updated)
        if not timedelta(minutes=-5) <= now - stamp <= timedelta(hours=24):
            raise ForecastError("forecast_not_current")
        updated.append(stamp)
        entities.append(state.entity_id)
        detailed = state.attributes.get("detailedForecast")
        if not isinstance(detailed, (list, tuple)) or not detailed:
            raise ForecastError("missing_detailed_forecast")
        for row in detailed:
            if not isinstance(row, Mapping):
                raise ForecastError("invalid_forecast_interval")
            start = row.get("period_start")
            rows.append(
                {**row, "period_start": start.isoformat() if isinstance(start, datetime) else start}
            )

    estimates = [
        power_forecast(
            [{**row, "pv_estimate": row.get(key)} for row in rows],
            now=now,
            issued_at=issued_at,
            unit="kW",
            required_until=required_until,
        )
        for key in ("pv_estimate", "pv_estimate10", "pv_estimate90")
    ]
    expected, lower, upper = estimates
    if any(
        not low.energy_wh <= mean.energy_wh <= high.energy_wh
        for mean, low, high in zip(expected, lower, upper, strict=True)
    ):
        raise ForecastError("unordered_uncertainty")
    return SolarForecast(expected, lower, upper, issued_at, min(updated), tuple(entities))
