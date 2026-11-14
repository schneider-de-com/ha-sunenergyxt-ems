"""Bind validated HA forecasts to an expiring hint in the existing controller."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import product
from time import monotonic
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import SUN_EVENT_SUNRISE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.sun import get_astral_event_date
from homeassistant.util import dt as dt_util

from .demand_forecast import DemandForecast, demand_forecast
from .forecast import ForecastError
from .forecast_evidence import ForecastEvidence
from .forecast_solar_source import ForecastSolarSource
from .forecast_sources import SolarForecast, solcast_forecast
from .logic import Battery, ControllerState
from .observer import DemandObserver
from .planning import PriorityPlan, plan_priority, validate_planning_inputs
from .registry import forecast_solar_entry, suggested_forecasts

if TYPE_CHECKING:
    from .runtime import EMSRuntime


@dataclass(frozen=True, slots=True)
class PlanningInputs:
    """Detached values for a worker which never touches HA or live state."""

    batteries: tuple[Battery, ...]
    capacities: dict[str, float]
    demand: DemandForecast
    solar: dict[str, SolarForecast]
    state: ControllerState
    grid_target_w: float


def _evaluate(inputs: PlanningInputs) -> PriorityPlan | None:
    decision = plan_priority(
        inputs.batteries,
        inputs.capacities,
        inputs.demand.expected,
        {key: value.expected for key, value in inputs.solar.items()},
        state=inputs.state,
        grid_target_w=inputs.grid_target_w,
    )
    if decision.order:
        keys = list(inputs.solar)
        choices = [
            (value.lower,) if value.lower == value.upper else (value.lower, value.upper)
            for value in inputs.solar.values()
        ]
        loads = (
            (inputs.demand.lower,)
            if inputs.demand.lower == inputs.demand.upper
            else (inputs.demand.lower, inputs.demand.upper)
        )
        for load, combination in product(loads, product(*choices)):
            sensitivity = plan_priority(
                inputs.batteries,
                inputs.capacities,
                load,
                dict(zip(keys, combination, strict=True)),
                state=inputs.state,
                grid_target_w=inputs.grid_target_w,
            )
            if sensitivity.order != decision.order:
                return None
    return decision


class HorizonPlanner:
    """Never write devices or change the household power budget."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, runtime: EMSRuntime, observer: DemandObserver
    ) -> None:
        self.hass, self.entry, self.runtime, self.observer = hass, entry, runtime, observer
        self.reason = "disabled"
        self.detail: str | None = None
        self.horizon_until: datetime | None = None
        self.decision: PriorityPlan | None = None
        self._next_evaluation: datetime | None = None
        self._task: asyncio.Task[None] | None = None
        self._generation = 0
        self._input_token: object = None
        self._closed = False
        self.forecast_solar_sources: dict[str, ForecastSolarSource] = {}
        self.forecast_evidence = ForecastEvidence(hass, entry.entry_id, runtime._publish)

    def _source_changed(self) -> None:
        if not self._closed:
            for source in self.forecast_solar_sources.values():
                if (snapshot := source.evidence_snapshot()) is not None:
                    self.forecast_evidence.submit(snapshot)
            self.invalidate()
            self.refresh()
            self.runtime._publish()

    def sync_forecast_sources(self) -> None:
        """Attach late owner caches even while control or other gates are off."""
        if self._closed:
            return
        mappings = [self.entry.options.get("pv_forecast", {})]
        per_battery = self.entry.options.get("pv_forecast_by_battery", {})
        if isinstance(per_battery, Mapping):
            mappings.extend(per_battery.values())
        selected: set[str] = set()
        for mapping in mappings:
            try:
                owner = forecast_solar_entry(self.hass, mapping)
            except ForecastError:
                continue
            if owner:
                selected.add(owner)
        for owner in selected:
            if owner not in self.forecast_solar_sources:
                self.forecast_solar_sources[owner] = ForecastSolarSource(
                    self.hass, self._source_changed
                )
            self.forecast_solar_sources[owner].bind(owner)
        for owner, source in self.forecast_solar_sources.items():
            if owner not in selected:
                source.bind(None)

    def invalidate(self) -> None:
        """Discard a decision immediately after operating options change."""
        self._next_evaluation = None
        self._fallback("invalid_data", "settings_changed")

    def _fallback(self, reason: str, detail: str | None = None) -> None:
        self._generation += 1
        self.runtime.controller.planning_order = ()
        self.runtime.controller.planning_expires_at = None
        self.reason, self.detail, self.decision = reason, detail, None

    async def async_stop(self) -> None:
        """Cancel result delivery; a running pure worker cannot touch hardware."""
        self._closed = True
        self._fallback("disabled")
        for source in self.forecast_solar_sources.values():
            await source.async_stop()
        await self.forecast_evidence.async_stop()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def _async_evaluate(
        self, inputs: PlanningInputs, generation: int, token: object, started: float
    ) -> None:
        try:
            decision = await self.hass.async_add_executor_job(_evaluate, inputs)
            if self._closed or generation != self._generation:
                return
            if monotonic() - started > 15:
                self._fallback("invalid_data", "planning_snapshot_expired")
                self._next_evaluation = None
                return
            # Recheck live eligibility after the worker yields the event loop.
            self.refresh()
            if generation != self._generation:
                return
            if token != self._input_token:
                self._fallback("invalid_data", "planning_snapshot_changed")
                self._next_evaluation = None
                return
            if decision is None:
                self._fallback("no_expected_advantage", "advantage_not_supported_by_forecast_range")
                return
            self.decision, self.reason = decision, decision.reason
            self.runtime.controller.planning_order = decision.order
            self.runtime.controller.planning_expires_at = (
                monotonic() + 90 if decision.order else None
            )
            self.runtime._publish()
        except ForecastError as exc:
            if generation == self._generation and not self._closed:
                self._fallback("invalid_data", str(exc))

    def _solar(self, mapping: Mapping[str, str], now: datetime, until: datetime) -> SolarForecast:
        if not isinstance(mapping, Mapping):
            raise ForecastError("invalid_forecast_mapping")
        owner = forecast_solar_entry(self.hass, mapping)
        if owner:
            source = self.forecast_solar_sources.get(owner)
            if source is None:
                raise ForecastError("forecast_solar_receipt_unknown")
            source.forecast(
                now=now,
                required_until=until,
                source_entities=(mapping["today"], mapping["tomorrow"]),
            )
            # A verified nominal curve is not a provider uncertainty interval.
            # Do not manufacture P10/P90 or authorize priorities from point data.
            raise ForecastError("forecast_uncertainty_not_supplied")
        ids = [mapping.get(role) for role in ("today", "tomorrow", "updated")]
        if any(not source for source in ids):
            raise ForecastError("forecast_sources_not_configured")
        registry = er.async_get(self.hass)
        registered = [registry.async_get(source) for source in ids if source]
        if (
            any(
                not item or item.platform != "solcast_solar" or not item.config_entry_id
                for item in registered
            )
            or len({item.config_entry_id for item in registered if item}) != 1
        ):
            raise ForecastError("forecast_source_entry_mismatch")
        states = [self.hass.states.get(source) for source in ids if source]
        return solcast_forecast(states[:2], states[2], now=now, required_until=until)

    def refresh(self) -> None:
        """Re-evaluate at most once a minute, invalidating quality failures at once."""
        now = dt_util.utcnow()
        if self._closed:
            return
        self.sync_forecast_sources()
        if not self.entry.options.get("planning_enabled", False):
            self._fallback("disabled")
            return
        if not self.runtime.controller.enabled:
            self._fallback("ems_disabled")
            return
        if not self.observer.persistence_ok:
            self._fallback("storage_unavailable")
            return
        if self.observer.reason != "measured":
            self._fallback("measurements_unavailable", self.observer.reason)
            return
        if (
            self.observer.last_poll_at is None
            or not timedelta() <= now - self.observer.last_poll_at <= timedelta(seconds=15)
        ):
            self._fallback("measurements_unavailable", "observer_not_current")
            return
        observed = self.runtime.household_observation()
        if observed.reason != "measured":
            self._fallback("measurements_unavailable", observed.reason)
            return
        batteries = self.runtime._batteries()
        if any(
            b.ev3600_charging is not False
            or (
                self.runtime.controller.feed_in
                and (
                    b.key in self.runtime.controller.state.full
                    or (b.soc is not None and b.upper_soc is not None and b.soc >= b.upper_soc)
                )
            )
            for b in batteries
        ):
            self._fallback("operating_priority")
            return
        local = now.astimezone(ZoneInfo(self.hass.config.time_zone))
        until = get_astral_event_date(
            self.hass, SUN_EVENT_SUNRISE, local.date() + timedelta(days=1)
        )
        if until is None:
            self._fallback("invalid_data", "night_horizon_unavailable")
            return
        self.horizon_until = until
        try:
            demand = demand_forecast(
                self.observer.history.intervals,
                now=now,
                required_until=until,
                timezone=ZoneInfo(self.hass.config.time_zone),
            )
        except ForecastError as exc:
            self._fallback("demand_history_unavailable", str(exc))
            return
        try:
            per_battery = self.entry.options.get("pv_forecast_by_battery", {})
            if not isinstance(per_battery, Mapping):
                raise ForecastError("invalid_forecast_mapping")
            if per_battery and not all(b.key in per_battery for b in batteries):
                self._fallback("pv_allocation_unavailable")
                return
            if all(b.key in per_battery for b in batteries):
                solar = {b.key: self._solar(per_battery[b.key], now, until) for b in batteries}
                day_sources = [per_battery[b.key].get("today") for b in batteries]
                if len(set(day_sources)) != len(day_sources):
                    raise ForecastError("pv_allocation_duplicated")
            else:
                aggregate = self._solar(
                    self.entry.options.get("pv_forecast") or suggested_forecasts(self.hass),
                    now,
                    until,
                )
                if len(batteries) > 1 and any(slot.energy_wh > 0 for slot in aggregate.upper):
                    self._fallback("pv_allocation_unavailable")
                    return
                solar = {b.key: aggregate for b in batteries}
        except ForecastError as exc:
            self._fallback("pv_forecast_unavailable", str(exc))
            return
        if self.runtime.controller.feed_in and any(
            slot.energy_wh > 0 for source in solar.values() for slot in source.upper
        ):
            self._fallback("operating_priority", "future_full_charge_export_not_modeled")
            return
        try:
            if not isinstance(self.entry.options.get("capacity_wh", {}), Mapping):
                raise ForecastError("storage_capacity_missing")
            validate_planning_inputs(
                batteries,
                self.entry.options.get("capacity_wh", {}),
                demand.expected,
                {key: value.expected for key, value in solar.items()},
                self.runtime.controller.grid_target_w,
            )
        except ForecastError as exc:
            self._fallback(
                "capacity_unavailable"
                if str(exc) == "storage_capacity_missing"
                else "invalid_data",
                str(exc),
            )
            return
        state = self.runtime.controller.state
        self._input_token = (
            deepcopy(dict(self.entry.options)),
            deepcopy(dict(self.entry.data)),
            batteries,
            state.main,
            state.support,
            tuple(sorted(state.blocked)),
            demand.history_end,
            len(self.observer.history.intervals),
            until,
            tuple(
                (key, value.issued_at, value.source_updated_at, value.source_entities)
                for key, value in solar.items()
            ),
            tuple(
                (entity, source.last_updated if (source := self.hass.states.get(entity)) else None)
                for entity in sorted(
                    {entity for value in solar.values() for entity in value.source_entities}
                )
            ),
        )
        if self._task is not None and not self._task.done():
            return
        if self._next_evaluation is not None and now < self._next_evaluation:
            return
        self._next_evaluation = now + timedelta(minutes=1)
        self._fallback("evaluating")
        inputs = PlanningInputs(
            batteries,
            dict(self.entry.options.get("capacity_wh", {})),
            demand,
            solar,
            deepcopy(state),
            self.runtime.controller.grid_target_w,
        )
        self._task = self.hass.async_create_task(
            self._async_evaluate(inputs, self._generation, self._input_token, monotonic()),
            eager_start=False,
        )
