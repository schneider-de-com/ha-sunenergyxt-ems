"""Pure EMS control policy. Home Assistant writes are handled elsewhere."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from math import ceil, isfinite


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
    output_limit_w: float = 1920


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


class Controller:
    """Compute inverter power limits; caller controls service writes and timing."""

    def __init__(self, state: ControllerState | None = None) -> None:
        self.state = deepcopy(state) if state else ControllerState()
        self.enabled = True
        self.feed_in = False
        self.grid_target_w = 20
        self.grid_grace_seconds = 8
        self.recovery_samples = 3

    def disable(self, batteries: tuple[Battery, ...]) -> dict[str, float]:
        self.enabled = False
        commands = self._safe_limits(batteries)
        self.state.outputs = commands.copy()
        return commands

    @staticmethod
    def _safe_limits(batteries: tuple[Battery, ...]) -> dict[str, float]:
        return {
            inv.key: inv.minimum_w
            for battery in batteries
            for inv in battery.inverters
            if inv.online
        }

    @staticmethod
    def _valid_soc(soc: float | None) -> bool:
        return soc is not None and isfinite(soc) and 0 <= soc <= 100

    def _eligible(self, batteries: tuple[Battery, ...]) -> list[Battery]:
        eligible = []
        for battery in batteries:
            if (
                not self._valid_soc(battery.soc)
                or not self._valid_soc(battery.lower_soc)
                or not any(inv.online for inv in battery.inverters)
            ):
                self.state.blocked.add(battery.key)
                continue
            assert battery.soc is not None and battery.lower_soc is not None
            if battery.soc <= battery.lower_soc:
                self.state.blocked.add(battery.key)
            elif battery.key in self.state.blocked and battery.soc >= battery.lower_soc + 5:
                self.state.blocked.remove(battery.key)
            if battery.key not in self.state.blocked:
                eligible.append(battery)
        return eligible

    def _rank(self, batteries: list[Battery]) -> list[Battery]:
        def score(battery: Battery) -> tuple[int, float, str]:
            assert battery.soc is not None and battery.lower_soc is not None
            delta = battery.soc - battery.lower_soc
            points = min(20, ceil(delta / 5))
            if (
                self._valid_soc(battery.upper_soc)
                and battery.charging_w > 40
                and battery.upper_soc is not None
            ):
                distance = battery.upper_soc - battery.soc
                points += (
                    3 if distance <= 3
                    else 2 if distance <= 5
                    else 1 if distance <= 10
                    else 0
                )
            if battery.key == self.state.main:
                points += 4
            elif battery.key == self.state.support:
                points += 2
            return (
                points,
                sum(inv.rated_w for inv in battery.inverters if inv.online),
                battery.key,
            )

        ranked = sorted(batteries, key=score, reverse=True)
        self.state.main = ranked[0].key if ranked else None
        self.state.support = ranked[1].key if len(ranked) > 1 else None
        return ranked

    def update(
        self,
        batteries: tuple[Battery, ...],
        grid_w: float | None,
        now: float,
        *,
        sequence: object | None = None,
    ) -> dict[str, float]:
        safe = self._safe_limits(batteries)
        if not self.enabled:
            return safe

        if grid_w is None or not isfinite(grid_w):
            if (
                self.state.last_grid_ok is not None
                and now - self.state.last_grid_ok <= self.grid_grace_seconds
                and not self.state.grid_outage
            ):
                return {
                    key: self.state.outputs.get(key, minimum)
                    for key, minimum in safe.items()
                }
            self.state.grid_outage = True
            self.state.recovery_count = 0
            self.state.last_sequence = None
            self.state.outputs = safe.copy()
            return safe

        if self.state.grid_outage:
            if sequence is not None and sequence != self.state.last_sequence:
                self.state.recovery_count += 1
                self.state.last_sequence = sequence
            if self.state.recovery_count < self.recovery_samples:
                return safe
            self.state.grid_outage = False
            self.state.recovery_count = 0
        self.state.last_grid_ok = now

        ranked = self._rank(self._eligible(batteries))
        current = sum(
            self.state.outputs.get(inv.key, 0)
            for battery in ranked
            for inv in battery.inverters
            if inv.online
        )
        total_w = max(0, current + grid_w - self.grid_target_w)
        commands = safe.copy()
        for battery in ranked:
            assert battery.soc is not None
            if (
                self.feed_in
                and self._valid_soc(battery.upper_soc)
                and battery.upper_soc is not None
            ):
                if battery.soc >= battery.upper_soc:
                    self.state.full.add(battery.key)
                elif battery.soc <= battery.upper_soc - 2:
                    self.state.full.discard(battery.key)
            else:
                self.state.full.discard(battery.key)

            eligible_inverters = [inv for inv in battery.inverters if inv.online]
            maximum_w = min(
                battery.output_limit_w, sum(inv.rated_w for inv in eligible_inverters)
            )
            budget_w = (
                maximum_w if battery.key in self.state.full else min(total_w, maximum_w)
            )
            for inverter in eligible_inverters:
                power_w = min(inverter.rated_w, max(inverter.minimum_w, budget_w))
                commands[inverter.key] = power_w
                budget_w = max(0, budget_w - power_w)
                total_w = max(0, total_w - power_w)
        self.state.outputs = commands.copy()
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
