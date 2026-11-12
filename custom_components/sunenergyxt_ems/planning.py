"""Compare forecast storage priorities without issuing power commands."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import timedelta

from .forecast import EnergyInterval, ForecastError, _utc
from .logic import Battery, Controller, ControllerState


@dataclass(frozen=True, slots=True)
class Replay:
    """Idealized energy balance, never a measured savings statement."""

    import_wh: float
    import_by_slot: tuple[float, ...]
    overflow_wh: float
    battery_discharge_wh: dict[str, float]
    final_energy_wh: dict[str, float]


@dataclass(frozen=True, slots=True)
class PriorityPlan:
    """Optional ranking hint; it cannot command power or add a reserve."""

    order: tuple[str, ...]
    reason: str
    expected_import_reduction_wh: float
    expected_advantage_wh: float
    minimum_tested_advantage_wh: float
    baseline: Replay
    planned: Replay


def _bounded(value: object, lower: float, upper: float) -> bool:
    return (
        not isinstance(value, bool) and isinstance(value, (int, float)) and lower <= value <= upper
    )


def validate_planning_inputs(
    batteries: Sequence[Battery],
    capacities: Mapping[str, float],
    demand: Sequence[EnergyInterval],
    pv: Mapping[str, Sequence[EnergyInterval]],
    grid_target_w: float,
) -> None:
    keys = {battery.key for battery in batteries}
    if not batteries or len(keys) != len(batteries):
        raise ForecastError("invalid_planning_storage")
    if keys != set(pv):
        raise ForecastError("pv_allocation_unavailable")
    if not _bounded(grid_target_w, 0, 200):
        raise ForecastError("invalid_grid_target")
    for battery in batteries:
        capacity = capacities.get(battery.key)
        if (
            isinstance(capacity, bool)
            or not isinstance(capacity, (int, float))
            or not 0 < capacity <= 1_000_000
        ):
            raise ForecastError("storage_capacity_missing")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 100
            for value in (battery.soc, battery.lower_soc, battery.upper_soc)
        ):
            raise ForecastError("invalid_planning_storage")
        if battery.soc is None or battery.lower_soc is None or battery.upper_soc is None:
            raise ForecastError("invalid_planning_storage")
        if (
            not battery.lower_soc <= battery.soc <= battery.upper_soc
            or battery.lower_soc >= battery.upper_soc
        ):
            raise ForecastError("invalid_planning_storage")
        if not _bounded(battery.output_limit_w, 0, 1920) or battery.output_limit_w == 0:
            raise ForecastError("invalid_planning_storage")
        if any(
            not _bounded(inv.rated_w, 0, 10_000) or inv.rated_w == 0 for inv in battery.inverters
        ):
            raise ForecastError("invalid_planning_storage")
    if not demand or any(len(pv[key]) != len(demand) for key in keys):
        raise ForecastError("incomplete_planning_horizon")
    previous = None
    for index, step in enumerate(demand):
        start, end = _utc(step.start), _utc(step.end)
        if not timedelta() < end - start <= timedelta(minutes=30) or (
            previous is not None and start != previous
        ):
            raise ForecastError("invalid_planning_horizon")
        previous = end
        for interval in [step, *(pv[key][index] for key in keys)]:
            if (
                (_utc(interval.start), _utc(interval.end)) != (start, end)
                or isinstance(interval.energy_wh, bool)
                or not isinstance(interval.energy_wh, (int, float))
                or not 0 <= interval.energy_wh <= 50_000
            ):
                raise ForecastError("invalid_planning_interval")
    if _utc(demand[-1].end) - _utc(demand[0].start) > timedelta(days=2):
        raise ForecastError("invalid_planning_horizon")


def _replay(
    batteries: Sequence[Battery],
    capacities: Mapping[str, float],
    demand: Sequence[EnergyInterval],
    pv: Mapping[str, Sequence[EnergyInterval]],
    state: ControllerState | None,
    order: tuple[str, ...],
    efficiency: float,
    grid_target_w: float,
) -> Replay:
    policy = Controller(state)
    policy.planning_order = order
    energy: dict[str, float] = {}
    headroom: dict[str, float] = {}
    discharge = {battery.key: 0.0 for battery in batteries}
    for battery in batteries:
        if battery.soc is None or battery.lower_soc is None or battery.upper_soc is None:
            raise ForecastError("invalid_planning_storage")
        energy[battery.key] = (battery.soc - battery.lower_soc) * capacities[battery.key] / 100
        headroom[battery.key] = (
            (battery.upper_soc - battery.lower_soc) * capacities[battery.key] / 100
        )
    imports: list[float] = []
    overflow = 0.0
    for index, load in enumerate(demand):
        hours = (_utc(load.end) - _utc(load.start)).total_seconds() / 3600
        available = {
            battery.key: energy[battery.key] + pv[battery.key][index].energy_wh
            for battery in batteries
        }
        virtual = []
        for battery in batteries:
            if battery.lower_soc is None:
                raise ForecastError("invalid_planning_storage")
            virtual.append(
                replace(
                    battery,
                    soc=min(
                        100,
                        battery.lower_soc + available[battery.key] / capacities[battery.key] * 100,
                    ),
                    charging_w=pv[battery.key][index].energy_wh / hours,
                )
            )
        ranked = policy._rank(policy._eligible(tuple(virtual)))
        remaining = max(0.0, load.energy_wh - grid_target_w * hours)
        served = 0.0
        for battery in ranked:
            maximum = min(
                min(1920.0, battery.output_limit_w) * efficiency,
                sum(inv.rated_w for inv in battery.inverters if inv.online),
            )
            supplied = min(remaining, available[battery.key] * efficiency, maximum * hours)
            dc_used = supplied / efficiency
            discharge[battery.key] += max(0.0, dc_used - pv[battery.key][index].energy_wh)
            available[battery.key] -= dc_used
            remaining -= supplied
            served += supplied
        imports.append(max(0.0, load.energy_wh - served))
        for battery in batteries:
            overflow += max(0.0, available[battery.key] - headroom[battery.key])
            energy[battery.key] = min(headroom[battery.key], max(0.0, available[battery.key]))
    return Replay(sum(imports), tuple(imports), overflow, discharge, energy)


def plan_priority(
    batteries: Sequence[Battery],
    capacities: Mapping[str, float],
    demand: Sequence[EnergyInterval],
    pv: Mapping[str, Sequence[EnergyInterval]],
    *,
    state: ControllerState | None = None,
    grid_target_w: float = 20,
) -> PriorityPlan:
    """Test each storage as main; keep baseline unless tested advantage is clear.

    Only order changes: ordinary household supply, all reserves and physical
    power limits remain authoritative. No grid charging or deliberate demand
    withholding exists in this model or its output.
    """
    validate_planning_inputs(batteries, capacities, demand, pv, grid_target_w)
    efficiencies = (0.8, 0.9, 1.0)
    baselines = [
        _replay(batteries, capacities, demand, pv, state, (), eta, grid_target_w)
        for eta in efficiencies
    ]
    baseline = baselines[1]
    result = PriorityPlan((), "no_expected_advantage", 0.0, 0.0, 0.0, baseline, baseline)
    # A two-percentage-point energy uncertainty allowance absorbs small SoC
    # quantization/switching effects. It is not a user acceptance tolerance.
    uncertainty_wh = sum(capacities[b.key] * 0.02 for b in batteries)
    for first in batteries:
        order = (first.key, *(b.key for b in batteries if b.key != first.key))
        trials = [
            _replay(batteries, capacities, demand, pv, state, order, eta, grid_target_w)
            for eta in efficiencies
        ]
        # Credit remaining usable energy equally on both sides. Emptying more
        # storage by the horizon end only shifts flat-price import in time.
        benefits = [
            base.import_wh
            - trial.import_wh
            - eta * (sum(base.final_energy_wh.values()) - sum(trial.final_energy_wh.values()))
            for base, trial, eta in zip(baselines, trials, efficiencies, strict=True)
        ]
        advantage, expected = min(benefits), benefits[1]
        if advantage > uncertainty_wh and expected > result.expected_advantage_wh:
            result = PriorityPlan(
                order,
                "expected_pv_loss_reduction",
                baseline.import_wh - trials[1].import_wh,
                expected,
                advantage,
                baseline,
                trials[1],
            )
    return result
