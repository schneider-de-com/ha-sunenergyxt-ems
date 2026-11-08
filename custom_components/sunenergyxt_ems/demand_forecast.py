"""Learn a conservative local-clock household profile from measured energy."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from .forecast import EnergyInterval, ForecastError, _utc


@dataclass(frozen=True, slots=True)
class DemandForecast:
    """Predicted energy and empirical range, not guaranteed consumption."""

    expected: tuple[EnergyInterval, ...]
    lower: tuple[EnergyInterval, ...]
    upper: tuple[EnergyInterval, ...]
    support_days: tuple[int, ...]
    history_end: datetime


def _bucket(stamp: datetime, timezone: ZoneInfo) -> int:
    local = stamp.astimezone(timezone)
    return local.hour * 2 + local.minute // 30


def demand_forecast(
    history: Sequence[EnergyInterval],
    *,
    now: datetime,
    required_until: datetime,
    timezone: ZoneInfo,
) -> DemandForecast:
    """Use complete measured half-hours, with a seven-day recency half-life.

    The HA boundary must establish genuine household energy and coverage before
    calling this function. Missing intervals are absent, never zero-filled. The
    three-day support floor is a conservative eligibility rule, not a calibrated
    statistical confidence claim. Repeated DST hours count as one local day.
    """
    now, until = _utc(now), _utc(required_until)
    if not timedelta() < until - now <= timedelta(days=2):
        raise ForecastError("invalid_horizon")
    buckets: dict[int, dict[date, list[EnergyInterval]]] = {}
    previous_end: datetime | None = None
    for sample in history:
        start, end = _utc(sample.start), _utc(sample.end)
        if (
            end - start != timedelta(minutes=30)
            or start.minute % 30
            or start.second
            or start.microsecond
            or end > now
            or (previous_end is not None and start < previous_end)
        ):
            raise ForecastError("invalid_demand_interval")
        previous_end = end
        value = sample.energy_wh
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not 0 <= value <= 50_000
        ):
            raise ForecastError("invalid_demand_energy")
        if end <= now - timedelta(days=28):
            continue
        normalized = EnergyInterval(start, end, float(value))
        local_date = start.astimezone(timezone).date()
        buckets.setdefault(_bucket(start, timezone), {}).setdefault(local_date, []).append(
            normalized
        )

    expected: list[EnergyInterval] = []
    lower: list[EnergyInterval] = []
    upper: list[EnergyInterval] = []
    support: list[int] = []
    slot = now.replace(minute=now.minute // 30 * 30, second=0, microsecond=0)
    while slot < until:
        days = buckets.get(_bucket(slot, timezone), {})
        if len(days) < 3:
            raise ForecastError("insufficient_demand_history")
        latest = max(samples[-1].end for samples in days.values())
        if now - latest > timedelta(days=3):
            raise ForecastError("stale_demand_history")
        daily_means = [
            sum(s.energy_wh for s in samples) / len(samples) for samples in days.values()
        ]
        weights = [
            2 ** (-(now - samples[-1].end).total_seconds() / (7 * 86400))
            for samples in days.values()
        ]
        mean = sum(
            value * weight for value, weight in zip(daily_means, weights, strict=True)
        ) / sum(weights)
        energies = [s.energy_wh for samples in days.values() for s in samples]
        start, end = max(slot, now), min(slot + timedelta(minutes=30), until)
        fraction = (end - start).total_seconds() / 1800
        expected.append(EnergyInterval(start, end, mean * fraction))
        lower.append(EnergyInterval(start, end, min(energies) * fraction))
        upper.append(EnergyInterval(start, end, max(energies) * fraction))
        support.append(len(days))
        slot += timedelta(minutes=30)
    return DemandForecast(
        tuple(expected),
        tuple(lower),
        tuple(upper),
        tuple(support),
        max(s.end for days in buckets.values() for samples in days.values() for s in samples),
    )
