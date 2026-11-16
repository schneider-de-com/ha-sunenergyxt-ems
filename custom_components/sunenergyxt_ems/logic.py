"""Pure EMS control policy. Home Assistant writes are handled elsewhere."""

from __future__ import annotations

from collections import deque
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from math import ceil, isfinite
from statistics import median

from .const import DEFAULT_TOWER_OUTPUT_LIMIT_W

# The legacy no-export deadband was 0..30 W around the 20 W default
# import target. Preserve its asymmetric -20/+10 W tolerance when users
# choose a different target; never include negative signed grid export.
GRID_DEADBAND_BELOW_TARGET_W = 20.0
GRID_DEADBAND_ABOVE_TARGET_W = 10.0


def weighted_battery_level(readings: list[tuple[float | None, float | None]]) -> float | None:
    """Return the capacity-weighted level only when every input is valid."""
    if not readings:
        return None
    for level, capacity in readings:
        if (
            level is None
            or capacity is None
            or isinstance(level, bool)
            or isinstance(capacity, bool)
            or not isfinite(level)
            or not isfinite(capacity)
            or not 0 <= level <= 100
            or capacity <= 0
        ):
            return None
    scale = max(capacity for _, capacity in readings if capacity is not None)
    total_capacity = sum(capacity / scale for _, capacity in readings if capacity is not None)
    weighted_sum = sum(
        level * (capacity / scale)
        for level, capacity in readings
        if level is not None and capacity is not None
    )
    return weighted_sum / total_capacity


@dataclass(frozen=True, slots=True)
class Inverter:
    key: str
    rated_w: float
    minimum_w: float = 0
    online: bool = True


@dataclass(frozen=True, slots=True)
class Battery:
    key: str
    soc: float | None
    lower_soc: float | None
    upper_soc: float | None
    inverters: tuple[Inverter, ...]
    charging_w: float = 0
    output_limit_w: float = DEFAULT_TOWER_OUTPUT_LIMIT_W
    dc_output_w: float | None = None
    pv_input_w: float | None = None
    pv_input_configured: bool = False
    ev3600_charging: bool | None = False
    # For identified BK hardware, require every attached module to report
    # reaching its BMS ceiling before FIRST entering the full-charge policy.
    # None retains the explicit/manual non-BK upper-SoC contract.
    bms_full_verified: bool | None = None


@dataclass(slots=True)
class ControllerState:
    outputs: dict[str, float] = field(default_factory=dict)
    blocked: set[str] = field(default_factory=set)
    full: set[str] = field(default_factory=set)
    main: str | None = None
    support: str | None = None
    last_grid_ok: float | None = None
    grid_outage: bool = False
    recovery_count: int = 0
    last_sequence: object | None = None
    ev3600_active: set[str] = field(default_factory=set)
    ev3600_unknown: set[str] = field(default_factory=set)
    raw_grid_w: float | None = None
    effective_grid_w: float | None = None
    regulation_error_w: float | None = None
    regulation_correction_w: float | None = None
    target_w: float = 0.0
    # Keep the previous confirmed multi-head split stable near the net target.
    cooperative_active: bool = False


class StartHold:
    """Hold normal corrections after an inverter first starts from zero."""

    def __init__(self, seconds: float = 30.0) -> None:
        self.seconds = seconds
        self._until: dict[str, float] = {}

    def record(self, key: str, *, before_w: float, after_w: float, now: float) -> None:
        if before_w <= 0 < after_w and isfinite(now):
            self._until[key] = now + self.seconds

    def remaining(self, key: str, now: float) -> float:
        return max(0.0, self._until.get(key, 0.0) - now)

    def active(self, key: str, now: float) -> bool:
        return self.remaining(key, now) > 0

    def reset(self, key: str | None = None) -> None:
        if key is None:
            self._until.clear()
        else:
            self._until.pop(key, None)


class GridMedianFilter:
    """Median of five distinct signed grid samples; never manufacture samples.

    Protects the load-following regulator against brief measured transients.
    Persistent export (at least three recent negative values) still reaches
    the controller. This does not suppress an urgent battery safety stop.
    """

    def __init__(self) -> None:
        self._samples: deque[float] = deque(maxlen=5)
        self._last_sequence: object | None = None

    def observe(self, grid_w: float, sequence: object) -> float:
        """Filter a new plausible meter sample, identified by its report time."""
        if not isfinite(grid_w) or abs(grid_w) > 100_000:
            self.reset()
            return grid_w
        if (
            self._samples
            and isinstance(sequence, datetime)
            and isinstance(self._last_sequence, datetime)
            and sequence <= self._last_sequence
        ):
            return float(median(self._samples))
        if not self._samples or sequence != self._last_sequence:
            self._samples.append(grid_w)
            self._last_sequence = sequence
        return float(median(self._samples))

    @property
    def last_sequence(self) -> object | None:
        """Identify the newest accepted meter observation."""
        return self._last_sequence

    def sustained_import(self) -> bool:
        """Confirm high import using three distinct recent meter samples."""
        # Three samples above 500 W separate sustained load from short spikes.
        return len(self._samples) >= 3 and all(value > 500 for value in list(self._samples)[-3:])

    def confirmed_import_w(self) -> float | None:
        """Conservative import budget from three distinct positive samples.

        Using the *smallest* recent import prevents a single appliance spike
        or a falling household load from immediately authorizing a large
        actuator increase. observe() already deduplicates meter sequences.
        """
        if len(self._samples) < 3:
            return None
        recent = list(self._samples)[-3:]
        if any(value <= 30.0 or not isfinite(value) for value in recent):
            return None
        return min(recent)

    def dynamic_target(self, requested_w: float) -> float:
        """Raise a user-chosen import reserve only for measured near-goal noise.

        Five distinct recent meter samples are required. Remote high-import
        load changes are not evidence for a larger steady-state buffer.
        The existing 200 W setup bound also applies to the effective reserve.
        """
        if not isfinite(requested_w) or len(self._samples) < 5:
            return requested_w
        recent = list(self._samples)
        if sum(abs(watts - requested_w) <= 150 for watts in recent) < 3:
            return requested_w
        # A single export pulse or appliance jump cannot inflate a quiet
        # import reserve: only consecutive pairs near the requested goal
        # provide evidence of ordinary steady-state meter noise.
        near_deltas = [
            abs(current - prior)
            for prior, current in zip(recent, recent[1:], strict=False)
            if abs(prior - requested_w) <= 150 and abs(current - requested_w) <= 150
        ]
        if len(near_deltas) < 2:
            return requested_w
        noise = median(near_deltas)
        margin = max(0.0, (noise - 30.0) * 0.25)
        return min(200.0, requested_w + margin)

    def rapid_import_decline(self) -> bool:
        """Two fresh, sizable downward steps mean the load is disappearing."""
        if not self.sustained_import():
            return False
        first, middle, last = list(self._samples)[-3:]
        # The existing 120 W control-step threshold is reused, not a device cap.
        return first - middle > 120 and middle - last > 120

    def confirmed_export(self) -> float | None:
        """Return the less severe of two consecutive significant export samples."""
        if len(self._samples) < 2:
            return None
        previous, current = list(self._samples)[-2:]
        if previous < -120 and current < -120:
            return max(previous, current)
        return None

    def reset(self) -> None:
        """Discard old meter samples after disable, restart or data loss."""
        self._samples.clear()
        self._last_sequence = None


class Controller:
    """Compute inverter power limits; caller controls service writes and timing."""

    def __init__(self, state: ControllerState | None = None) -> None:
        self.state = deepcopy(state) if state else ControllerState()
        self.enabled = True
        self.feed_in = False
        self.full_hysteresis_pct = 2.0
        self.grid_target_w = 20
        self.grid_grace_seconds = 8
        self.recovery_samples = 3
        self.planning_order: tuple[str, ...] = ()
        self.planning_expires_at: float | None = None
        # Set by the runtime only after three distinct high-import meter reports.
        self.cooperative_dispatch = False
        self.effective_grid_target_w: float | None = None

    def _grid_target(self) -> float:
        value = self.effective_grid_target_w
        if value is None or not isfinite(value):
            return self.grid_target_w
        return max(self.grid_target_w, min(200.0, value))

    def _within_grid_deadband(self, grid_w: float) -> bool:
        """Use the same legacy tolerance around the *effective* import target."""
        target = self._grid_target()
        return (
            max(0.0, target - GRID_DEADBAND_BELOW_TARGET_W)
            <= grid_w
            < target + GRID_DEADBAND_ABOVE_TARGET_W
        )

    def _reset_regulation(self) -> None:
        """Invalidate previous correction data on disable, outage or recovery."""
        self.state.regulation_error_w = None
        self.state.regulation_correction_w = None

    def disable(self, batteries: tuple[Battery, ...]) -> dict[str, float]:
        self.enabled = False
        self.state.cooperative_active = False
        commands = self._safe_limits(batteries)
        self.state.outputs = commands.copy()
        self._reset_regulation()
        return commands

    @staticmethod
    def _safe_limits(batteries: tuple[Battery, ...]) -> dict[str, float]:
        return {
            inv.key: inv.minimum_w
            for battery in batteries
            for inv in battery.inverters
            if Controller._valid_inverter(inv)
        }

    @staticmethod
    def _valid_output_limit(value: float) -> bool:
        return isfinite(value) and value > 0

    @staticmethod
    def _valid_inverter(inv: Inverter) -> bool:
        return (
            inv.online
            and bool(inv.key)
            and isfinite(inv.rated_w)
            and isfinite(inv.minimum_w)
            and inv.rated_w > 0
            and 0 <= inv.minimum_w <= inv.rated_w
        )

    @staticmethod
    def _valid_soc(soc: float | None) -> bool:
        return soc is not None and isfinite(soc) and 0 <= soc <= 100

    def _eligible(self, batteries: tuple[Battery, ...]) -> list[Battery]:
        eligible = []
        self.state.ev3600_active.clear()
        self.state.ev3600_unknown.clear()
        for battery in batteries:
            if (
                not self._valid_soc(battery.soc)
                or not self._valid_soc(battery.lower_soc)
                or not self._valid_output_limit(battery.output_limit_w)
                or not any(self._valid_inverter(inv) for inv in battery.inverters)
            ):
                self.state.blocked.add(battery.key)
                self.state.full.discard(battery.key)
                continue
            if battery.soc is None or battery.lower_soc is None:
                continue
            if battery.soc <= battery.lower_soc:
                self.state.blocked.add(battery.key)
            elif battery.key in self.state.blocked and battery.soc >= battery.lower_soc + 5:
                self.state.blocked.remove(battery.key)
            if battery.ev3600_charging is True:
                self.state.full.discard(battery.key)
                self.state.ev3600_active.add(battery.key)
            elif battery.ev3600_charging is None:
                self.state.full.discard(battery.key)
                self.state.ev3600_unknown.add(battery.key)
            elif battery.key not in self.state.blocked:
                eligible.append(battery)
        return eligible

    @staticmethod
    def _fresh_pv_budget(battery: Battery) -> bool:
        """Runtime owns PV sample freshness; the policy rejects missing/bad input."""
        return (
            battery.pv_input_configured
            and battery.pv_input_w is not None
            and isfinite(battery.pv_input_w)
            and 0 <= battery.pv_input_w <= 10000
        )

    def _rank(self, batteries: list[Battery]) -> list[Battery]:
        original_order = {battery.key: index for index, battery in enumerate(batteries)}

        def score(battery: Battery) -> tuple[int, int, float, int]:
            if battery.soc is None or battery.lower_soc is None:
                return (-1, 0, 0, 0)
            delta = battery.soc - battery.lower_soc
            points = min(20, ceil(delta / 5))
            if (
                self._valid_soc(battery.upper_soc)
                and battery.charging_w > 40
                and battery.upper_soc is not None
            ):
                distance = battery.upper_soc - battery.soc
                points += 3 if distance <= 3 else 2 if distance <= 5 else 1 if distance <= 10 else 0
            if battery.key == self.state.main:
                points += 4
            elif battery.key == self.state.support:
                points += 2
            return (
                points,
                2
                if battery.key == self.state.main
                else 1
                if battery.key == self.state.support
                else 0,
                sum(inv.rated_w for inv in battery.inverters if inv.online),
                -original_order[battery.key],
            )

        ranked = sorted(batteries, key=score, reverse=True)
        if self.planning_order and len(set(self.planning_order)) == len(self.planning_order):
            priority = {key: index for index, key in enumerate(self.planning_order)}
            ranked.sort(key=lambda battery: priority.get(battery.key, len(priority)))
        self.state.main = ranked[0].key if ranked else None
        self.state.support = ranked[1].key if len(ranked) > 1 else None
        return ranked

    @staticmethod
    def _cooperative_allocation(batteries: list[Battery], requested_w: float) -> dict[str, float]:
        """Weighted water-filling; never spend another head\'s reserve/budget."""
        budgets = {
            b.key: min(b.output_limit_w, sum(i.rated_w for i in b.inverters if i.online))
            for b in batteries
        }
        allocations = {b.key: 0.0 for b in batteries}
        weights = {
            b.key: max(0.0, (b.soc or 0.0) - (b.lower_soc or 0.0) - 5.0) * budgets[b.key]
            for b in batteries
        }
        remaining = min(max(0.0, requested_w), sum(budgets.values()))
        while remaining > 0.001:
            candidates = [
                b.key
                for b in batteries
                if weights[b.key] > 0 and budgets[b.key] - allocations[b.key] > 0.001
            ]
            total_weight = sum(weights[key] for key in candidates)
            if total_weight <= 0:
                # Any tiny margin near reserve belongs to the conventional
                # ranked allocation; do not force a new 0->positive restart.
                break
            step = remaining
            supplied = 0.0
            for key in candidates:
                portion = min(budgets[key] - allocations[key], step * weights[key] / total_weight)
                allocations[key] += portion
                supplied += portion
            remaining -= supplied
            if supplied <= 0.001:
                break
        if remaining > 0.001:
            # Explicitly ranked, reserve-respecting fallback for excess demand.
            for b in batteries:
                spare = max(0.0, budgets[b.key] - allocations[b.key])
                addition = min(remaining, spare)
                allocations[b.key] += addition
                remaining -= addition
                if remaining <= 0.001:
                    break
        return allocations

    def update(
        self,
        batteries: tuple[Battery, ...],
        grid_w: float | None,
        now: float,
        *,
        sequence: object | None = None,
    ) -> dict[str, float]:
        safe = self._safe_limits(batteries)
        if (
            self.planning_expires_at is None
            or not isfinite(self.planning_expires_at)
            or now >= self.planning_expires_at
        ):
            self.planning_order = ()
        if not self.enabled:
            self._reset_regulation()
            return safe

        eligible = self._eligible(batteries)
        eligible_keys = {
            inv.key
            for battery in eligible
            for inv in battery.inverters
            if self._valid_inverter(inv)
        }
        time_ok = isfinite(now) and (
            self.state.last_grid_ok is None or now >= self.state.last_grid_ok
        )
        if not time_ok or grid_w is None or not isfinite(grid_w) or abs(grid_w) > 100_000:
            if (
                time_ok
                and self.state.last_grid_ok is not None
                and now - self.state.last_grid_ok <= self.grid_grace_seconds
                and not self.state.grid_outage
            ):
                held = {
                    key: self.state.outputs.get(key, minimum) if key in eligible_keys else minimum
                    for key, minimum in safe.items()
                }
                self.state.outputs = held.copy()
                return held
            self.state.grid_outage = True
            self.state.cooperative_active = False
            self.state.recovery_count = 0
            self.state.last_sequence = None
            self.state.outputs = safe.copy()
            self._reset_regulation()
            return safe

        if self.state.grid_outage:
            if sequence is not None and sequence != self.state.last_sequence:
                self.state.recovery_count += 1
                self.state.last_sequence = sequence
            if self.state.recovery_count < self.recovery_samples:
                return safe
            self.state.grid_outage = False
            self.state.recovery_count = 0
            self._reset_regulation()
        self.state.last_grid_ok = now

        ranked = self._rank(eligible)
        current = sum(
            self.state.outputs.get(inv.key, 0)
            for battery in ranked
            for inv in battery.inverters
            if self._valid_inverter(inv)
        )
        # Single feedback policy for every installation: asymmetric signed
        # grid response, effective target deadband and bounded corrections.
        # Adaptive metering, inverter startup hold and ACK barriers stay in
        # the runtime and cannot be bypassed by a legacy PID option.
        error_w = grid_w - self._grid_target()
        if self._within_grid_deadband(grid_w):
            correction = 0.0
        else:
            gain = 1.0 if grid_w < 0 else 0.5
            # Cap the response to avoid large 0→positive Deye steps.
            step_cap = 20.0 if abs(grid_w) < 30 else 120.0 if abs(grid_w) <= 150 else 520.0
            correction = max(-step_cap, min(step_cap, error_w * gain))
        self.state.regulation_error_w = error_w
        self.state.regulation_correction_w = correction
        self.state.raw_grid_w = grid_w
        self.state.effective_grid_w = grid_w
        # Leaving a full-charge export override must not continue intentional
        # export merely to respect the ordinary rate limit.
        exiting_full = any(
            battery.key in self.state.full
            and (
                not self.feed_in
                or not self._fresh_pv_budget(battery)
                or not self._valid_soc(battery.upper_soc)
                or battery.bms_full_verified is False
                or (
                    battery.soc is not None
                    and battery.upper_soc is not None
                    and battery.soc <= battery.upper_soc - self.full_hysteresis_pct
                )
            )
            for battery in ranked
        )
        if exiting_full and grid_w <= self.grid_target_w:
            # A stale/missing PV source revokes the prior export allowance
            # immediately, even when the meter is at zero rather than negative.
            # Fresh positive import remains ordinary household load-following.
            correction = -current
        total_w = max(0, current + correction)
        commands = safe.copy()
        # A verified high-import period starts cooperative dispatch. Once both
        # towers are contributing, preserve that allocation through ordinary
        # near-target adjustments instead of forcing another 0% start.
        can_share = len(ranked) > 1 and not self.state.full
        self.state.cooperative_active = can_share and (
            self.cooperative_dispatch or self.state.cooperative_active
        )
        shared_budgets = (
            self._cooperative_allocation(ranked, total_w) if self.state.cooperative_active else {}
        )
        for battery in ranked:
            if battery.soc is None:
                continue
            eligible_inverters = [inv for inv in battery.inverters if self._valid_inverter(inv)]
            maximum_w = min(battery.output_limit_w, sum(inv.rated_w for inv in eligible_inverters))
            pv_ready = self._fresh_pv_budget(battery)
            if (
                pv_ready
                and battery.pv_input_w is not None
                and sum(inv.minimum_w for inv in eligible_inverters) > battery.pv_input_w
            ):
                # A positive hardware minimum must not exceed the fresh PV budget.
                pv_ready = False

            if (
                self.feed_in
                and pv_ready
                and self._valid_soc(battery.upper_soc)
                and battery.upper_soc is not None
            ):
                if (
                    battery.bms_full_verified is False
                    or battery.soc <= battery.upper_soc - self.full_hysteresis_pct
                ):
                    self.state.full.discard(battery.key)
                elif battery.soc >= battery.upper_soc:
                    # A fresh full-module BMS observation is required to enter
                    # the policy; hysteresis preserves an already-entered state.
                    self.state.full.add(battery.key)
            else:
                self.state.full.discard(battery.key)

            if battery.key in self.state.full and battery.pv_input_w is not None:
                budget_w = min(maximum_w, battery.pv_input_w)
            else:
                # Missing PV evidence resumes ordinary household load-following,
                # never unbounded full-charge battery discharge.
                budget_w = min(shared_budgets.get(battery.key, total_w), maximum_w)
            for inverter in eligible_inverters:
                power_w = min(inverter.rated_w, max(inverter.minimum_w, budget_w))
                commands[inverter.key] = power_w
                budget_w = max(0, budget_w - power_w)
                total_w = max(0, total_w - power_w)
        self.state.outputs = commands.copy()
        self.state.target_w = sum(commands.values())
        return commands


def policy(
    batteries: tuple[Battery, ...],
    grid_w: float | None,
    state: ControllerState,
    now: float,
) -> tuple[dict[str, float], ControllerState]:
    """Return next outputs and state without mutating the caller's state."""
    engine = Controller(state)
    return engine.update(batteries, grid_w, now), engine.state
