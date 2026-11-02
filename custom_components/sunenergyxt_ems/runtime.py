"""Home Assistant runtime: one writer for the existing inverter limit entities."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from math import isfinite
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_time_interval

from .const import (
    CONF_BATTERIES,
    CONF_DEVICE,
    CONF_ENABLED,
    CONF_FEED_IN,
    CONF_GRID,
    CONF_GRID_INVERT,
    CONF_INVERTERS,
    CONF_LOWER,
    CONF_RATED_W,
    CONF_SOC,
    CONF_UPPER,
    DOMAIN,
)
from .logic import Battery, Controller, Inverter

_LOGGER = logging.getLogger(__name__)
UPDATE_PERIOD = timedelta(seconds=5)
MAX_GRID_AGE = timedelta(seconds=30)
MIN_OUTPUT_CHANGE_W = 10.0


class EMSRuntime:
    """Read Home Assistant inputs and apply safe inverter limits."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.controller = Controller()
        self.controller.enabled = bool(entry.options.get(CONF_ENABLED, True))
        self.controller.feed_in = bool(entry.options.get(CONF_FEED_IN, False))
        self.status = "waiting"
        self.last_error: str | None = None
        self._unsubscribe: Any = None
        self._lock = asyncio.Lock()
        self._last_sent: dict[str, float] = {}
        self._sent_at: dict[str, float] = {}
        self._running = False
        self._seen_valid = False

    @property
    def signal(self) -> str:
        return f"{DOMAIN}_changed_{self.entry.entry_id}"

    def _publish(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    async def async_start(self) -> None:
        """Start polling. Never send commands using data from a previous run."""
        self._running = True
        self._unsubscribe = async_track_time_interval(
            self.hass,
            lambda _: self.hass.async_create_task(self.async_update()),
            UPDATE_PERIOD,
        )
        await self.async_update()

    async def async_stop(self) -> None:
        """Stop running without an unsafe surprise write during HA shutdown."""
        self._running = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    def _float(self, source: str | float | int | None) -> float | None:
        if isinstance(source, (int, float)):
            number = float(source)
        elif isinstance(source, str):
            state = self.hass.states.get(source)
            if state is None or state.state in ("unknown", "unavailable"):
                return None
            try:
                number = float(state.state)
            except (TypeError, ValueError):
                return None
        else:
            return None
        return number if isfinite(number) else None

    def _batteries(self) -> tuple[Battery, ...]:
        batteries = []
        for record in self.entry.data[CONF_BATTERIES]:
            inverters = []
            for definition in record[CONF_INVERTERS]:
                entity_id = definition["entity_id"]
                state = self.hass.states.get(entity_id)
                unit = state.attributes.get("unit_of_measurement") if state else None
                rated = self._float(definition[CONF_RATED_W])
                if rated is None or rated <= 0:
                    continue
                online = bool(
                    state
                    and state.state not in ("unknown", "unavailable")
                    and unit in ("W", "%")
                )
                minimum = self._float(state.attributes.get("min", 0)) if state else 0
                minimum = max(0.0, minimum or 0.0)
                if unit == "%":
                    minimum = minimum / 100 * rated
                inverters.append(
                    Inverter(
                        entity_id, rated_w=rated, minimum_w=minimum, online=online
                    )
                )
            charging = self._float(record.get("charging")) or 0
            batteries.append(Battery(
                key=record[CONF_DEVICE],
                soc=self._float(record.get(CONF_SOC)),
                lower_soc=self._float(record.get(CONF_LOWER)),
                upper_soc=self._float(record.get(CONF_UPPER)),
                inverters=tuple(inverters),
                charging_w=charging,
            ))
        return tuple(batteries)

    def _grid(self) -> tuple[float | None, object | None]:
        state = self.hass.states.get(self.entry.data[CONF_GRID])
        if state is None:
            return None, None
        sample_time = getattr(state, "last_reported", None) or state.last_updated
        if datetime.now(UTC) - sample_time > MAX_GRID_AGE:
            return None, None
        number = self._float(state.state)
        if number is None:
            return None, None
        if state.attributes.get("unit_of_measurement") == "kW":
            number *= 1000
        elif state.attributes.get("unit_of_measurement") != "W":
            return None, None
        if self.entry.data.get(CONF_GRID_INVERT):
            number = -number
        return number, sample_time

    def _minimum_interval(self, entity_id: str) -> float:
        """Prefer conservative intervals for APsystems flash-based controls."""
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry and "apsystems" in entry.platform.lower():
            return 300.0
        return 15.0

    async def _apply(self, targets: dict[str, float], now: float) -> None:
        for entity_id, target in targets.items():
            state = self.hass.states.get(entity_id)
            if state is None or state.state in ("unknown", "unavailable"):
                continue
            unit = state.attributes.get("unit_of_measurement")
            rating = next(
                (
                    float(inv[CONF_RATED_W])
                    for battery in self.entry.data[CONF_BATTERIES]
                    for inv in battery[CONF_INVERTERS]
                    if inv["entity_id"] == entity_id
                ),
                0.0,
            )
            if not 0 < rating <= 5000 or unit not in ("W", "%"):
                continue
            value = target * 100 / rating if unit == "%" else target
            minimum = self._float(state.attributes.get("min", 0))
            maximum = self._float(state.attributes.get("max", 100 if unit == "%" else rating))
            step = self._float(state.attributes.get("step", 1)) or 1
            if minimum is None or maximum is None or maximum < minimum:
                continue
            value = max(minimum, min(maximum, value))
            value = round(round((value - minimum) / step) * step + minimum, 4)
            previous = self._last_sent.get(entity_id)
            interval = self._minimum_interval(entity_id)
            if previous is not None:
                if abs(value - previous) < max(
                    step, MIN_OUTPUT_CHANGE_W * (100 / rating if unit == "%" else 1)
                ):
                    continue
                # Safety reductions are not delayed by ordinary command throttling.
                if value > previous and now - self._sent_at.get(entity_id, 0) < interval:
                    continue
            try:
                await self.hass.services.async_call(
                    "number", "set_value",
                    {"entity_id": entity_id, "value": value},
                    blocking=True,
                )
            except (HomeAssistantError, ValueError) as exc:
                self.last_error = f"{entity_id}: {exc}"
                self.status = "write_error"
                _LOGGER.warning("Unable to set %s limit: %s", entity_id, exc)
                continue
            self._last_sent[entity_id] = value
            self._sent_at[entity_id] = now

    async def async_update(self) -> None:
        """One serialized update; never trust stale meter readings."""
        if not self._running or self._lock.locked():
            return
        async with self._lock:
            self.controller.feed_in = bool(self.entry.options.get(CONF_FEED_IN, False))
            self.controller.enabled = bool(self.entry.options.get(CONF_ENABLED, True))
            if not self.controller.enabled:
                self.status = "disabled"
                self._publish()
                return
            now = monotonic()
            grid, sequence = self._grid()
            batteries = self._batteries()
            if not self._seen_valid:
                if grid is None or not any(
                    battery.soc is not None
                    and battery.lower_soc is not None
                    and any(inverter.online for inverter in battery.inverters)
                    for battery in batteries
                ):
                    self.status = "waiting"
                    self._publish()
                    return
                self._seen_valid = True
            outputs = self.controller.update(
                batteries, grid, now, sequence=sequence
            )
            self.last_error = None
            self.status = "running" if grid is not None else "grid_unavailable"
            await self._apply(outputs, now)
            self._publish()

    async def async_disable(self) -> None:
        """One-shot safe command when the user deliberately disables EMS."""
        async with self._lock:
            self.controller.enabled = False
            commands = self.controller.disable(self._batteries())
            await self._apply(commands, monotonic())
            self.status = "disabled" if self.last_error is None else "write_error"
            self._publish()

    async def async_change_options(self) -> None:
        """Apply an options-flow update without restarting the control loop."""
        enabled = bool(self.entry.options.get(CONF_ENABLED, True))
        self.controller.feed_in = bool(self.entry.options.get(CONF_FEED_IN, False))
        if not enabled and self.controller.enabled:
            await self.async_disable()
        elif enabled and not self.controller.enabled:
            self.controller.enabled = True
            self._seen_valid = False
            self.status = "waiting"
            await self.async_update()
        self._publish()
