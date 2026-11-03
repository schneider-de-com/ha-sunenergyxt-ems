"""Home Assistant runtime: one writer for the existing inverter limit entities."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
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
    CONF_FEED_IN_MODE,
    FEED_IN_MODE_PV,
    CONF_GRID,
    CONF_GRID_INVERT,
    CONF_INVERTERS,
    CONF_LOWER,
    CONF_RATED_W,
    CONF_SOC,
    CONF_UPPER,
    DEFAULT_GRID_TARGET_W,
    DOMAIN,
)
from .logic import Battery, Controller, Inverter

_LOGGER = logging.getLogger(__name__)
UPDATE_PERIOD = timedelta(seconds=5)
MAX_GRID_AGE = timedelta(seconds=30)
MAX_SOC_AGE = timedelta(seconds=45)
MAX_DC_AGE = timedelta(seconds=90)
MAX_BK215_DC_W = 1920.0
MIN_OUTPUT_CHANGE_W = 10.0
FEEDBACK_SETTLE_SECONDS = 5.0
FEEDBACK_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True, slots=True)
class PendingCommand:
    """A service call is pending until HA reports the requested limit."""

    target: float
    entity_reported_before: datetime
    sent_at: float
    grid_reported_before: datetime | None


class EMSRuntime:
    """Read Home Assistant inputs and apply safe inverter limits."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.controller = Controller()
        self.controller.enabled = bool(entry.options.get(CONF_ENABLED, True))
        self.controller.feed_in = bool(entry.options.get(CONF_FEED_IN, False)) and entry.options.get(CONF_FEED_IN_MODE, FEED_IN_MODE_PV) == FEED_IN_MODE_PV
        self.status = "waiting"
        self.last_error: str | None = None
        self._unsubscribe: Any = None
        self._lock = asyncio.Lock()
        self._last_sent: dict[str, float] = {}
        self._sent_at: dict[str, float] = {}
        self._pending: dict[str, PendingCommand] = {}
        self._shutdown_pending: dict[str, PendingCommand] = {}
        self._overload_latched: set[str] = set()
        self._last_processed_grid: datetime | None = None
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
            self._async_on_interval,
            UPDATE_PERIOD,
        )
        await self.async_update()

    async def _async_on_interval(self, _now: datetime) -> None:
        """Run scheduled updates in Home Assistant's event loop, never its executor."""
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
            # An entity ID and an HA state string are both strings.
            # First accept numeric states; then resolve entity IDs.
            try:
                number = float(source)
            except ValueError:
                state = self.hass.states.get(source)
                if state is None or state.state in ("unknown", "unavailable"):
                    return None
                try:
                    number = float(state.state)
                except TypeError, ValueError:
                    return None
        else:
            return None
        return number if isfinite(number) else None

    def _fresh_soc(self, source: str | float | int | None) -> float | None:
        """Never discharge based on an old battery measurement."""
        if isinstance(source, str) and source.startswith("sensor."):
            state = self.hass.states.get(source)
            if state is None:
                return None
            sample_time = getattr(state, "last_reported", None) or state.last_updated
            age = datetime.now(UTC) - sample_time
            if age < timedelta(seconds=-5) or age > MAX_SOC_AGE:
                return None
        return self._float(source)

    def _fresh_dc_output(self, source: str | None) -> float | None:
        """Read recent BK215 DC output watts or return no usable measurement."""
        if not source:
            return None
        state = self.hass.states.get(source)
        if state is None or state.attributes.get("unit_of_measurement") != "W":
            return None
        stamp = getattr(state, "last_reported", None) or state.last_updated
        age = datetime.now(UTC) - stamp
        if age < timedelta(seconds=-5) or age > MAX_DC_AGE:
            return None
        value = self._float(state.state)
        return value if value is not None and 0 <= value <= 10000 else None

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
                    state and state.state not in ("unknown", "unavailable") and unit in ("W", "%")
                )
                minimum = self._float(state.attributes.get("min", 0)) if state else 0
                minimum = max(0.0, minimum or 0.0)
                if unit == "%":
                    minimum = minimum / 100 * rated
                inverters.append(
                    Inverter(entity_id, rated_w=rated, minimum_w=minimum, online=online)
                )
            charging = self._float(record.get("charging")) or 0
            batteries.append(
                Battery(
                    key=record[CONF_DEVICE],
                    soc=self._fresh_soc(record.get(CONF_SOC)),
                    lower_soc=self._float(record.get(CONF_LOWER)),
                    upper_soc=self._float(record.get(CONF_UPPER)),
                    inverters=tuple(inverters),
                    charging_w=charging,
                    dc_output_w=self._fresh_dc_output(record.get("dc_output")),
                )
            )
        return tuple(batteries)

    def _grid(self) -> tuple[float | None, object | None]:
        state = self.hass.states.get(self.entry.data[CONF_GRID])
        if state is None:
            return None, None
        sample_time = getattr(state, "last_reported", None) or state.last_updated
        age = datetime.now(UTC) - sample_time
        if age < timedelta(seconds=-5) or age > MAX_GRID_AGE:
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

    def _reported_outputs(self, batteries: tuple[Battery, ...]) -> dict[str, float]:
        """Read reported inverter limit entities, not unexecuted planned outputs."""
        observed: dict[str, float] = {}
        for battery in batteries:
            for inverter in battery.inverters:
                if not inverter.online:
                    continue
                state = self.hass.states.get(inverter.key)
                value = self._float(state.state) if state else None
                if value is None or state is None:
                    continue
                unit = state.attributes.get("unit_of_measurement")
                power_w = value * inverter.rated_w / 100 if unit == "%" else value
                if unit in ("%", "W") and 0 <= power_w <= inverter.rated_w:
                    observed[inverter.key] = power_w
        return observed

    def _feedback_ready(self, now: float, grid_seq: datetime | None) -> bool:
        """Wait for a changed reported limit and a later, settled meter sample."""
        if not self._pending:
            return True
        for entity_id, pending in self._pending.items():
            state = self.hass.states.get(entity_id)
            current = self._float(state.state) if state else None
            reported = (
                (getattr(state, "last_reported", None) or state.last_updated) if state else None
            )
            step = self._float(state.attributes.get("step", 1)) if state else None
            confirmed = (
                current is not None
                and isinstance(reported, datetime)
                and reported > pending.entity_reported_before
                and abs(current - pending.target) <= max((step or 1) / 2, 0.01)
            )
            if not confirmed:
                if now - pending.sent_at >= FEEDBACK_TIMEOUT_SECONDS:
                    self.status = "write_error"
                    self.last_error = (
                        f"{entity_id}: command was not acknowledged within "
                        f"{FEEDBACK_TIMEOUT_SECONDS:g} seconds"
                    )
                else:
                    self.status = "waiting_feedback"
                return False
            if (
                now - pending.sent_at < FEEDBACK_SETTLE_SECONDS
                or grid_seq is None
                or (
                    pending.grid_reported_before is not None
                    and grid_seq <= pending.grid_reported_before
                )
            ):
                self.status = "waiting_meter"
                return False
        self._pending.clear()
        self.last_error = None
        return True

    def _reserve_reductions(self, batteries: tuple[Battery, ...]) -> dict[str, float]:
        """Urgent reductions bypass normal acknowledgement waiting."""
        self.controller._eligible(batteries)
        observed = self._reported_outputs(batteries)
        reductions: dict[str, float] = {}
        for battery in batteries:
            if battery.key not in self.controller.state.blocked:
                continue
            for inverter in battery.inverters:
                if not inverter.online:
                    continue
                requested = self._last_sent.get(inverter.key, 0.0)
                current = observed.get(inverter.key, 0.0)
                if max(requested, current) > inverter.minimum_w:
                    reductions[inverter.key] = inverter.minimum_w
        return reductions

    def _minimum_interval(self, entity_id: str) -> float:
        """Prefer conservative intervals for APsystems flash-based controls."""
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry and "apsystems" in entry.platform.lower():
            return 300.0
        return 5.0

    async def _apply(
        self, targets: dict[str, float], now: float, *, emergency: bool = False
    ) -> None:
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
            value = min(
                maximum, max(minimum, round(round((value - minimum) / step) * step + minimum, 4))
            )
            previous = self._last_sent.get(entity_id)
            # No-op writes on startup only create unnecessary communication
            # (and a fake pending acknowledgement) for already-correct limits.
            reported_value = self._float(state.state)
            pending = self._pending.get(entity_id)
            if (
                emergency
                and pending is not None
                and abs(pending.target - value) < 0.01
                and now - pending.sent_at < FEEDBACK_TIMEOUT_SECONDS
            ):
                continue
            if (
                previous is None
                and reported_value is not None
                and abs(reported_value - value) <= max(step / 2, 0.01)
            ):
                continue
            interval = self._minimum_interval(entity_id)
            if previous is not None:
                if abs(value - previous) < max(
                    step, MIN_OUTPUT_CHANGE_W * (100 / rating if unit == "%" else 1)
                ) and not (emergency and reported_value is not None and value < reported_value):
                    continue
                elapsed = now - self._sent_at.get(entity_id, 0)
                # Safety fallback and deliberate shutdown may bypass the normal interval.
                if not (
                    emergency
                    and (
                        value < previous or (reported_value is not None and value < reported_value)
                    )
                ):
                    required = max(interval, 20.0) if value > previous else interval
                    if elapsed < required:
                        continue
            reported_before = getattr(state, "last_reported", None) or state.last_updated
            _, grid_before = self._grid()
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": value},
                    blocking=True,
                )
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if task is not None and task.cancelling():
                    raise
                self.last_error = f"{entity_id}: Solarman request was cancelled"
                self.status = "write_error"
                _LOGGER.warning("Inverter command cancelled: %s", entity_id)
                continue
            except (HomeAssistantError, TimeoutError, OSError, ValueError) as exc:
                self.last_error = f"{entity_id}: {exc}"
                self.status = "write_error"
                _LOGGER.warning("Unable to set %s limit: %s", entity_id, exc)
                continue
            self._last_sent[entity_id] = value
            self._sent_at[entity_id] = now
            self._pending[entity_id] = PendingCommand(
                target=value,
                entity_reported_before=reported_before,
                sent_at=now,
                grid_reported_before=grid_before,
            )

    def _verify_shutdown(self, now: float) -> None:
        """Confirm each zero-limit actuator report; never assume service success."""
        failures = []
        for entity_id, pending in list(self._shutdown_pending.items()):
            state = self.hass.states.get(entity_id)
            current = self._float(state.state) if state else None
            reported = (
                (getattr(state, "last_reported", None) or state.last_updated) if state else None
            )
            step = self._float(state.attributes.get("step", 1)) if state else None
            if (
                current is not None
                and isinstance(reported, datetime)
                and reported > pending.entity_reported_before
                and abs(current - pending.target) <= max((step or 1) / 2, 0.01)
            ):
                self._shutdown_pending.pop(entity_id, None)
            elif now - pending.sent_at >= FEEDBACK_TIMEOUT_SECONDS:
                failures.append(entity_id)
        if failures:
            self.last_error = "Shutdown not acknowledged: " + ", ".join(failures)
            self.status = "write_error"
        elif self._shutdown_pending:
            self.status = "shutdown_pending"
        elif self.status != "write_error":
            self.last_error = None
            self.status = "disabled"

    async def async_update(self) -> None:
        """One serialized update; never trust stale meter readings."""
        if not self._running or self._lock.locked():
            return
        async with self._lock:
            self.controller.feed_in = bool(self.entry.options.get(CONF_FEED_IN, False)) and self.entry.options.get(CONF_FEED_IN_MODE, FEED_IN_MODE_PV) == FEED_IN_MODE_PV
            self.controller.enabled = bool(self.entry.options.get(CONF_ENABLED, True))
            if not self.controller.enabled:
                self._verify_shutdown(monotonic())
                self._publish()
                return
            now = monotonic()
            self.controller.grid_target_w = float(
                self.entry.options.get("grid_target_w", DEFAULT_GRID_TARGET_W)
            )
            grid, sequence = self._grid()
            batteries = self._batteries()
            self._overload_latched.update(
                battery.key
                for battery in batteries
                if battery.dc_output_w is not None and battery.dc_output_w > MAX_BK215_DC_W
            )
            if self._overload_latched:
                safe = {
                    inv.key: inv.minimum_w
                    for battery in batteries
                    if battery.key in self._overload_latched
                    for inv in battery.inverters
                    if inv.online
                }
                self.last_error = None
                await self._apply(safe, now, emergency=True)
                if self.last_error is None:
                    self.status = "hardware_limit"
                    self.last_error = (
                        "BK215 DC output above 1920 W; manual inspection required: "
                        + ", ".join(sorted(self._overload_latched))
                    )
                self._publish()
                return
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
            # Protect the battery independently of normal feedback settling.
            # Do not increase another inverter until an earlier required
            # reduction has been written and acknowledged.
            reserve_stop = self._reserve_reductions(batteries)
            if reserve_stop:
                await self._apply(reserve_stop, now, emergency=True)
                if self.last_error is None:
                    self.status = "reserve_safety"
                self._publish()
                return

            if grid is None:
                outputs = self.controller.update(batteries, grid, now, sequence=sequence)
                self.status = "grid_unavailable"
                await self._apply(outputs, now, emergency=True)
                self._publish()
                return

            if not self._feedback_ready(now, sequence):
                self._publish()
                return

            # A 5 s scheduler tick does not imply a new meter observation.
            if sequence == self._last_processed_grid:
                self.status = "waiting_meter"
                self._publish()
                return

            # The policy uses the reported, acknowledged actuator limits,
            # never speculative commands from earlier, rate-limited ticks.
            self.controller.state.outputs = self._reported_outputs(batteries)
            previous_main = self.controller.state.main
            outputs = self.controller.update(batteries, grid, now, sequence=sequence)
            self._last_processed_grid = sequence

            if previous_main and previous_main != self.controller.state.main:
                old_system = next(
                    (battery for battery in batteries if battery.key == previous_main),
                    None,
                )
                if old_system is not None:
                    old_limits = self._reported_outputs((old_system,))
                    release = {
                        inv.key: inv.minimum_w
                        for inv in old_system.inverters
                        if inv.online
                        and (
                            old_limits.get(inv.key, 0) > inv.minimum_w
                            or self._last_sent.get(inv.key, 0) > inv.minimum_w
                        )
                    }
                    if release:
                        await self._apply(release, now, emergency=True)
                        if self.last_error is None:
                            self.status = "switching_main"
                        self._publish()
                        return

            self.last_error = None
            self.status = "running"
            await self._apply(outputs, now)
            if self.last_error is None and self._pending:
                self.status = "waiting_feedback"
            # Show requested limits only when actually sent. If a write was
            # rate-limited, keep the confirmed baseline in public diagnostics.
            if not self._pending:
                self.controller.state.outputs = self._reported_outputs(batteries)
            self._publish()

    async def async_disable(self) -> None:
        """One-shot safe command when the user deliberately disables EMS."""
        async with self._lock:
            self.controller.enabled = False
            self.last_error = None
            self._pending.clear()
            self._shutdown_pending.clear()
            commands = self.controller.disable(self._batteries())
            configured = {
                inverter["entity_id"]
                for battery in self.entry.data[CONF_BATTERIES]
                for inverter in battery[CONF_INVERTERS]
            }
            unreachable = sorted(configured - commands.keys())
            await self._apply(commands, monotonic(), emergency=True)
            self._shutdown_pending = dict(self._pending)
            self._pending.clear()
            observed = self._reported_outputs(self._batteries())
            missed = [
                key
                for key, target in commands.items()
                if key not in self._shutdown_pending and observed.get(key, 0) > target + 10
            ]
            if missed:
                self.last_error = "Shutdown command not dispatched: " + ", ".join(missed)
            if unreachable:
                self.last_error = (
                    (self.last_error + "; " if self.last_error else "")
                    + "Cannot stop unavailable inverters: "
                    + ", ".join(unreachable)
                )
            self.status = (
                "write_error"
                if self.last_error is not None
                else "shutdown_pending"
                if self._shutdown_pending
                else "disabled"
            )
            self._publish()

    async def async_change_options(self) -> None:
        """Apply an options-flow update without restarting the control loop."""
        enabled = bool(self.entry.options.get(CONF_ENABLED, True))
        self.controller.feed_in = bool(self.entry.options.get(CONF_FEED_IN, False)) and self.entry.options.get(CONF_FEED_IN_MODE, FEED_IN_MODE_PV) == FEED_IN_MODE_PV
        if not enabled and self.controller.enabled:
            await self.async_disable()
        elif enabled and not self.controller.enabled:
            self.controller.enabled = True
            self._seen_valid = False
            self.status = "waiting"
            await self.async_update()
        self._publish()
