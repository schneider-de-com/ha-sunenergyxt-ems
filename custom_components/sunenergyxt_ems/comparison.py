"""Internal same-input pricing of model replays; never a control input."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isfinite

from .forecast import EnergyInterval, ForecastError, _utc
from .logic import Battery, ControllerState
from .planning import Replay, plan_priority


@dataclass(frozen=True, slots=True)
class PriceInterval:
    """Verified EUR/kWh over a half-open absolute interval."""

    start: datetime
    end: datetime
    eur_per_kwh: float


@dataclass(frozen=True, slots=True)
class CostedReplay:
    """Model outputs, with unsupported physical quantities left unknown."""

    import_wh: float
    flat_import_eur: float | None
    dynamic_import_eur: float | None
    modeled_overflow_dc_wh: float
    battery_discharge_dc_wh: dict[str, float]
    final_usable_dc_wh: dict[str, float]
    export_wh: None = None
    measured_curtailment_wh: None = None


@dataclass(frozen=True, slots=True)
class Comparison:
    """One modeled baseline/plan pair under separate tariff scenarios."""

    baseline: CostedReplay
    planned: CostedReplay
    order: tuple[str, ...]
    reason: str
    expected_advantage_wh: float
    mode: str = "internal_model_not_measured_savings"
    dynamic_use: str = "hypothetical_pricing_no_control"


def _price(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ForecastError("invalid_price")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ForecastError("invalid_price") from exc
    if not isfinite(number):
        raise ForecastError("invalid_price")
    return number


def gpe_prices(response: object, *, unit: str) -> tuple[PriceInterval, ...]:
    """Parse the verified GPE get_prices response, including negative prices.

    The caller verifies the integration's EUR/kWh unit and provenance. This
    pure parser neither fetches prices nor establishes the household's tariff.
    """
    if unit != "EUR/kWh" or not isinstance(response, Mapping):
        raise ForecastError("invalid_price_contract")
    rows = response.get("prices")
    if not isinstance(rows, list):
        raise ForecastError("invalid_price_contract")
    result: list[PriceInterval] = []
    for row in rows:
        if not isinstance(row, Mapping):
            raise ForecastError("invalid_price_contract")
        start, end = row.get("start"), row.get("end")
        if not isinstance(start, str) or not isinstance(end, str):
            raise ForecastError("invalid_price_time")
        try:
            first, last = _utc(datetime.fromisoformat(start)), _utc(datetime.fromisoformat(end))
        except ValueError as exc:
            raise ForecastError("invalid_price_time") from exc
        if last - first != timedelta(minutes=15) or (result and first != result[-1].end):
            raise ForecastError("noncontiguous_price_intervals")
        result.append(PriceInterval(first, last, _price(row.get("price"))))
    return tuple(result)


def _dynamic_cost(
    replay: Replay, demand: Sequence[EnergyInterval], prices: Sequence[PriceInterval]
) -> float:
    # The model's import energy is uniform within each demand slot; this is
    # an explicit temporal-resolution assumption, not quarter-hour metering.
    total = 0.0
    for slot, imported in zip(demand, replay.import_by_slot, strict=True):
        first, last = _utc(slot.start), _utc(slot.end)
        cursor = first
        for price in prices:
            start, end = _utc(price.start), _utc(price.end)
            if end <= cursor or start >= last:
                continue
            if start > cursor:
                raise ForecastError("price_coverage_missing")
            overlap_end = min(end, last)
            fraction = (overlap_end - cursor) / (last - first)
            total += imported / 1000 * fraction * _price(price.eur_per_kwh)
            cursor = overlap_end
            if cursor == last:
                break
        if cursor != last:
            raise ForecastError("price_coverage_missing")
    return total


def compare(
    batteries: Sequence[Battery],
    capacities: Mapping[str, float],
    demand: Sequence[EnergyInterval],
    pv: Mapping[str, Sequence[EnergyInterval]],
    *,
    flat_eur_per_kwh: float | None = None,
    dynamic_prices: Sequence[PriceInterval] | None = None,
    state: ControllerState | None = None,
    grid_target_w: float = 20,
) -> Comparison:
    """Replay both strategies once on identical inputs, then price the results.

    Prices are deliberately not passed to the planner. Missing tariff data is
    unknown, never zero. Period import bills exclude export revenue and do not
    value final stored energy; inspect the planner's separate terminal-energy
    adjusted advantage before interpreting differing period bills as benefit.
    """
    flat = _price(flat_eur_per_kwh) if flat_eur_per_kwh is not None else None
    if dynamic_prices is not None:
        previous = None
        for price in dynamic_prices:
            start, end = _utc(price.start), _utc(price.end)
            if end - start != timedelta(minutes=15) or (previous is not None and start != previous):
                raise ForecastError("noncontiguous_price_intervals")
            _price(price.eur_per_kwh)
            previous = end
    decision = plan_priority(
        batteries, capacities, demand, pv, state=state, grid_target_w=grid_target_w
    )

    def costed(replay: Replay) -> CostedReplay:
        return CostedReplay(
            replay.import_wh,
            None if flat is None else replay.import_wh / 1000 * flat,
            None if dynamic_prices is None else _dynamic_cost(replay, demand, dynamic_prices),
            replay.overflow_wh,
            dict(replay.battery_discharge_wh),
            dict(replay.final_energy_wh),
        )

    return Comparison(
        costed(decision.baseline),
        costed(decision.planned),
        decision.order,
        decision.reason,
        decision.expected_advantage_wh,
    )
