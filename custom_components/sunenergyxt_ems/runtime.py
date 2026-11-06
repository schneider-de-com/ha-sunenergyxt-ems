"""Home Assistant runtime: one writer for the existing inverter limit entities."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from math import isfinite
from time import monotonic
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, Event, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval
from homeassistant.helpers.storage import Store

from .const import (
    CONF_BATTERIES,
    CONF_DC_TRIPPED,
    CONF_DEVICE,
    CONF_ENABLED,
    CONF_FEED_IN,
    CONF_FEED_IN_HYSTERESIS,
    CONF_FEED_IN_MODE,
    CONF_GRID,
    CONF_GRID_INVERT,
    CONF_INVERTERS,
    CONF_LOWER,
    CONF_RATED_W,
    CONF_SOC,
    CONF_UPPER,
    DEFAULT_GRID_TARGET_W,
    DOMAIN,
    FEED_IN_MODE_PV,
)
from .inputs import ev3600_charging, fresh_power
from .logic import Battery, Controller, GridMedianFilter, Inverter, StartHold
from .pid import PIDGains
from .registry import registered_connection, registered_dc_output

_LOGGER = logging.getLogger(__name__)
UPDATE_PERIOD = timedelta(seconds=5)
MAX_GRID_AGE = timedelta(seconds=30)
MAX_SOC_AGE = timedelta(seconds=45)
MAX_DC_AGE = timedelta(seconds=90)
MAX_BK215_DC_W = 1920.0
MIN_OUTPUT_CHANGE_W = 10.0
# Live 2026-11-04: an initial 520 W / 26% M200 command produced
# -646 W net export after a transient 2.5 kW household load. Bound each
# upward *actuator* step to the project's existing 20 W smallest step.
# This is a conservative software ramp, not a device/hardware power cap.
MAX_POSITIVE_COMMAND_STEP_W = 20.0
FEEDBACK_SETTLE_SECONDS = 5.0
FEEDBACK_TIMEOUT_SECONDS = 30.0
SHUTDOWN_RETRY_BACKOFF_SECONDS = 300.0
# Conservative software-only startup export trip. On 2026-11-04 the
# M200 produced -188.632 W signed-grid export after a *nominal* 20 W
# command; the old -200 W threshold missed this observed unsafe sample.
# -100 W is an operational fail-closed threshold, NOT a device rating
# or a claim about the actual AC power output of the inverter.
STARTUP_EXPORT_TRIP_W = -100.0


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
        self.controller.feed_in = (
            bool(entry.options.get(CONF_FEED_IN, False))
            and entry.options.get(CONF_FEED_IN_MODE, FEED_IN_MODE_PV) == FEED_IN_MODE_PV
        )
        self.controller.full_hysteresis_pct = float(entry.options.get(CONF_FEED_IN_HYSTERESIS, 2.0))
        self.status = "waiting"
        self.last_error: str | None = None
        self._unsubscribe: Any = None
        self._unsubscribe_connections: Any = None
        self._connection_inverters: dict[str, set[str]] = {}
        self._lock = asyncio.Lock()
        self._last_sent: dict[str, float] = {}
        self._sent_at: dict[str, float] = {}
        self._pending: dict[str, PendingCommand] = {}
        self._shutdown_pending: dict[str, PendingCommand] = {}
        self._shutdown_retried: set[str] = set()
        self._shutdown_failed_at: dict[str, float] = {}
        self._deferred_shutdown: set[str] = set()
        self._shutdown_obligations: set[str] = set()
        self._inflight_commands: set[str] = set()
        # A previously acknowledged shutdown must be rechecked after a
        # genuine unavailable -> online transition, not on every off tick.
        self._offline_inverters: set[str] = set()
        self._shutdown_store: Store[dict[str, list[str]]] = Store(
            hass, 1, f"{DOMAIN}.shutdown.{entry.entry_id}", atomic_writes=True
        )
        self._overload_latched: set[str] = set(entry.options.get(CONF_DC_TRIPPED, ()))
        self._dc_monitored: dict[str, str] = {}
        self._last_processed_grid: datetime | None = None
        self._grid_filter = GridMedianFilter()
        self._grid_raw_w: float | None = None
        self._grid_filtered_w: float | None = None
        self._last_raw_grid_sequence: datetime | None = None
        self._startup_export_trip = False
        self._start_hold = StartHold(seconds=30.0)
        self._running = False
        self._seen_valid = False

    @property
    def signal(self) -> str:
        return f"{DOMAIN}_changed_{self.entry.entry_id}"

    def _publish(self) -> None:
        async_dispatcher_send(self.hass, self.signal)

    async def async_start(self) -> None:
        """Start polling. Never send commands using data from a previous run."""
        await self._load_shutdown_obligations()
        self._running = True
        # A real M80 Solarman outage on 2026-11-03 lasted 4.83 s,
        # shorter than the 5 s control interval: event capture is required.
        links: dict[str, set[str]] = {}
        for inverter in self._configured_inverters():
            connection = registered_connection(self.hass, inverter)
            if connection is not None:
                links.setdefault(connection, set()).add(inverter)
        self._connection_inverters = links
        if links:
            self._unsubscribe_connections = async_track_state_change_event(
                self.hass, sorted(links), self._on_connection_event
            )
        self._unsubscribe = async_track_time_interval(
            self.hass,
            self._async_on_interval,
            UPDATE_PERIOD,
        )
        await self.async_update()

    async def _load_shutdown_obligations(self) -> None:
        """Recover unacknowledged shutdowns without assuming an old command succeeded."""
        try:
            saved = await self._shutdown_store.async_load()
        except (OSError, HomeAssistantError) as exc:
            _LOGGER.warning("Cannot read inverter shutdown journal: %s", exc)
            saved = None
        pending = saved.get("pending") if isinstance(saved, dict) else None
        inflight = saved.get("inflight", []) if isinstance(saved, dict) else None
        if (
            isinstance(pending, list)
            and all(isinstance(key, str) for key in pending)
            and isinstance(inflight, list)
            and all(isinstance(key, str) for key in inflight)
        ):
            self._inflight_commands = set(inflight)
            self._shutdown_obligations = set(pending) | self._inflight_commands
        else:
            # First use and a lost journal are indistinguishable. Establish a
            # confirmed minimum once, even when the restored EMS switch is off.
            self._shutdown_obligations = self._configured_inverters()
            if self._shutdown_obligations:
                await self._save_shutdown_obligations()
        self._deferred_shutdown = set(self._shutdown_obligations)

    @callback
    def _on_connection_event(self, event: Event) -> None:
        """Latch even very short outages; immediately check safe-off on return."""
        if not self._running:
            return
        inverters = self._connection_inverters.get(event.data.get("entity_id"), set())
        new_state = event.data.get("new_state")
        if not inverters or new_state is None or new_state.state != "on":
            self._offline_inverters.update(inverters)
            return
        if self._offline_inverters.intersection(inverters):
            # The number entity can remain online throughout a Solarman
            # dropout; the next regular polling tick could miss it entirely.
            self.hass.async_create_task(self.async_update())

    def _configured_inverters(self) -> set[str]:
        return {
            inverter["entity_id"]
            for battery in self.entry.data[CONF_BATTERIES]
            for inverter in battery[CONF_INVERTERS]
        }

    async def _save_shutdown_obligations(self, pending: set[str] | None = None) -> bool:
        """Persist before sending, and remove only after fresh acknowledgement."""
        try:
            data = {
                "pending": sorted(self._shutdown_obligations if pending is None else pending),
                "inflight": sorted(self._inflight_commands),
            }
            await self._shutdown_store.async_save(data)
            # Store can defer writes during shutdown and only log disk write errors.
            if (
                getattr(self.hass, "state", None) is CoreState.stopping
                or await self._shutdown_store.async_load() != data
            ):
                raise OSError("Home Assistant did not confirm the shutdown journal write")
        except (OSError, HomeAssistantError) as exc:
            self.last_error = f"Cannot persist inverter shutdown obligations: {exc}"
            self.status = "write_error"
            _LOGGER.error("%s", self.last_error)
            return False
        return True

    async def _async_on_interval(self, _now: datetime) -> None:
        """Run scheduled updates in Home Assistant's event loop, never its executor."""
        await self.async_update()

    async def async_stop(self) -> None:
        """Stop running without an unsafe surprise write during HA shutdown."""
        self._running = False
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        if self._unsubscribe_connections is not None:
            self._unsubscribe_connections()
            self._unsubscribe_connections = None

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
                connection_id = registered_connection(self.hass, entity_id)
                connection = self.hass.states.get(connection_id) if connection_id else None
                online = bool(
                    state
                    and state.state not in ("unknown", "unavailable")
                    and unit in ("W", "%")
                    # The M80's limit remained readable even through a
                    # 2026-11-03 Solarman disconnect. Trust the actual link.
                    and (not connection_id or (connection and connection.state == "on"))
                )
                minimum = self._float(state.attributes.get("min", 0)) if state else 0
                minimum = max(0.0, minimum or 0.0)
                if unit == "%":
                    minimum = minimum / 100 * rated
                inverters.append(
                    Inverter(entity_id, rated_w=rated, minimum_w=minimum, online=online)
                )
            charging = self._float(record.get("charging")) or 0
            dc_source = (
                record.get("dc_output")
                or self._dc_monitored.get(record[CONF_DEVICE])
                or registered_dc_output(self.hass, record[CONF_DEVICE])
            )
            if dc_source:
                self._dc_monitored[record[CONF_DEVICE]] = dc_source
            batteries.append(
                Battery(
                    key=record[CONF_DEVICE],
                    soc=self._fresh_soc(record.get(CONF_SOC)),
                    lower_soc=self._float(record.get(CONF_LOWER)),
                    upper_soc=self._float(record.get(CONF_UPPER)),
                    inverters=tuple(inverters),
                    charging_w=charging,
                    dc_output_w=self._fresh_dc_output(dc_source),
                    pv_input_w=fresh_power(
                        self.hass, self.entry.options.get("pv_input", {}).get(record[CONF_DEVICE])
                    ),
                    pv_input_configured=bool(
                        self.entry.options.get("pv_input", {}).get(record[CONF_DEVICE])
                    ),
                    ev3600_charging=ev3600_charging(
                        self.hass, self.entry.options.get("ev3600", {}).get(record[CONF_DEVICE])
                    ),
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

    async def _hardware_feedback_matches(self, entity_id: str, target: float) -> bool:
        """Verify a Solarman limit by a fresh device register read, not an optimistic HA state.

        Only the explicitly identified deye_micro_53 profile is supported:
        both connected M200/M80 were checked on 2026-11-04 (1% -> 100,
        0% -> 0 at register 53). Unknown Solarman profiles fail closed.
        Non-Solarman platforms retain their existing HA-entity feedback.
        """
        registered = er.async_get(self.hass).async_get(entity_id)
        if registered is None or registered.platform != "solarman":
            return True
        if not registered.config_entry_id or not registered.device_id:
            return False
        source_entry = self.hass.config_entries.async_get_entry(registered.config_entry_id)
        if source_entry is None or source_entry.options.get("lookup_file") != "deye_micro_53.yaml":
            return False
        state = self.hass.states.get(entity_id)
        if state is None or state.attributes.get("unit_of_measurement") != "%":
            return False
        try:
            # Solarman's number.write() publishes the requested state without a
            # fresh register read. This read service executes a new Modbus
            # READ_HOLDING_REGISTERS request, independent of the optimistic state.
            response = await self.hass.services.async_call(
                "solarman",
                "read_holding_registers",
                {"device": registered.device_id, "address": 53, "count": 1},
                blocking=True,
                return_response=True,
            )
        except (HomeAssistantError, TimeoutError, OSError, ValueError) as exc:
            _LOGGER.debug("Cannot verify %s with Solarman register 53: %s", entity_id, exc)
            return False
        if not isinstance(response, dict):
            return False
        raw = self._float(response.get("53", response.get(53)))
        # The installed profile encodes percentages as 100 register units per 1%.
        # This conversion is profile-specific, not a universal Deye or Modbus rule.
        return raw is not None and abs(raw / 100.0 - target) <= 0.05

    async def _feedback_ready(self, now: float, grid_seq: datetime | None) -> bool:
        """Wait for a changed reported limit and a later, settled meter sample."""
        if not self._pending:
            return True
        waiting_feedback = False
        timed_out = False
        waiting_meter = False
        for entity_id, pending in list(self._pending.items()):
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
            if confirmed:
                confirmed = await self._hardware_feedback_matches(entity_id, pending.target)
            if not confirmed:
                if now - pending.sent_at >= FEEDBACK_TIMEOUT_SECONDS:
                    self._shutdown_obligations.add(entity_id)
                    self._deferred_shutdown.add(entity_id)
                    await self._save_shutdown_obligations()
                    self._pending.pop(entity_id, None)
                    minimum = next(
                        (
                            inv.minimum_w
                            for battery in self._batteries()
                            for inv in battery.inverters
                            if inv.key == entity_id and inv.online
                        ),
                        None,
                    )
                    if minimum is not None:
                        await self._apply({entity_id: minimum}, now, emergency=True, force=True)
                        if entity_id in self._pending:
                            self._shutdown_pending[entity_id] = self._pending.pop(entity_id)
                            self._deferred_shutdown.discard(entity_id)
                    timed_out = True
                    self.last_error = (
                        f"{entity_id}: command was not acknowledged within "
                        f"{FEEDBACK_TIMEOUT_SECONDS:g} seconds"
                    )
                else:
                    waiting_feedback = True
                continue
            if (
                now - pending.sent_at < FEEDBACK_SETTLE_SECONDS
                or grid_seq is None
                or (
                    pending.grid_reported_before is not None
                    and grid_seq <= pending.grid_reported_before
                )
            ):
                waiting_meter = True
        if timed_out or waiting_feedback or waiting_meter:
            self.status = (
                "write_error"
                if timed_out
                else "waiting_feedback"
                if waiting_feedback
                else "waiting_meter"
            )
            return False
        completed = set(self._pending) & self._inflight_commands
        if completed:
            self._inflight_commands.difference_update(completed)
            if not await self._save_shutdown_obligations():
                self._inflight_commands.update(completed)
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
            if battery.key not in (
                self.controller.state.blocked
                | self.controller.state.ev3600_active
                | self.controller.state.ev3600_unknown
            ):
                continue
            for inverter in battery.inverters:
                if not inverter.online:
                    continue
                requested = self._last_sent.get(inverter.key, 0.0)
                current = observed.get(inverter.key, 0.0)
                if max(requested, current) > inverter.minimum_w:
                    reductions[inverter.key] = inverter.minimum_w
        return reductions

    def _reconnected_inverters(self, batteries: tuple[Battery, ...]) -> set[str]:
        """Detect actual HA availability recovery without policing manual limits.

        Once the user hands a running inverter back to manual control, its
        later limit changes are left alone. Only a witnessed outage/recovery
        creates a fresh obligation to re-establish the safe minimum.
        """
        offline = {
            inverter.key
            for battery in batteries
            for inverter in battery.inverters
            if not inverter.online
        }
        online = {
            inverter.key
            for battery in batteries
            for inverter in battery.inverters
            if inverter.online
        }
        recovered = self._offline_inverters & online
        self._offline_inverters.difference_update(recovered)
        self._offline_inverters.update(offline)
        return recovered

    def _minimum_interval(self, entity_id: str) -> float:
        """Prefer conservative intervals for APsystems flash-based controls."""
        entry = er.async_get(self.hass).async_get(entity_id)
        if entry and "apsystems" in entry.platform.lower():
            return 300.0
        return 5.0

    async def _apply(
        self, targets: dict[str, float], now: float, *, emergency: bool = False, force: bool = False
    ) -> None:
        for entity_id, target in targets.items():
            high_import = (
                self._grid_raw_w is not None
                and self._grid_filter.sustained_import()
                and not self.controller.feed_in
                and not self._start_hold.active(entity_id, now)
            )
            interval = self._minimum_interval(entity_id)
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
            if not emergency and not force:
                # Rate-limit the *actually dispatched* actuator command, not
                # the speculative pure-controller target. The latter may be
                # hundreds of watts after a single short-lived load spike.
                # A missing numeric baseline cannot authorize an increase.
                reported = self._float(state.state)
                if reported is None:
                    continue
                baseline_w = reported * rating / 100 if unit == "%" else reported
                previous_command = self._last_sent.get(entity_id)
                if previous_command is not None:
                    previous_w = (
                        previous_command * rating / 100 if unit == "%" else previous_command
                    )
                    baseline_w = min(baseline_w, previous_w)
                # The nominal 20 W budget is NOT a safe physical step on
                # Deye microinverters: at 1% the M80 (8 W nominal) made
                # phase B drop ~73 W in a guarded trial, and the M200 has
                # shown worse transients. Start fine percent actuators
                # with one precision step (0.1pp maximum). Once a previous command
                # is acknowledged and startup has settled, use the normal
                # 20 W budget. Applying 0.1pp for the entire run made a
                # 240 W household load take roughly 27 minutes to settle.
                precision = self._float(state.attributes.get("step", 1))
                positive_step_w = MAX_POSITIVE_COMMAND_STEP_W
                if unit == "%" and precision is not None and 0 < precision <= 0.1:
                    if (
                        previous_command is not None
                        and previous_command - reported >= precision / 2
                        and target > baseline_w
                    ):
                        continue
                    if (
                        previous_command is None
                        or baseline_w <= 0
                        or self._start_hold.active(entity_id, now)
                    ):
                        positive_step_w = min(positive_step_w, rating * precision / 100)
                    # A device can report 2.29% for a confirmed 2.3% command.
                    # Use that command's grid point only within its rounding
                    # interval, after feedback settles and a fresh device check.
                    if (
                        previous_command is not None
                        and 0 < previous_command - reported < precision / 2
                        and target > previous_command * rating / 100
                        and entity_id not in self._pending
                        and now - self._sent_at.get(entity_id, 0)
                        >= (interval if high_import else max(interval, 20.0))
                        and await self._hardware_feedback_matches(entity_id, previous_command)
                    ):
                        baseline_w = previous_command * rating / 100
                target = min(target, baseline_w + positive_step_w)
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
            if not emergency and not force:
                # A coarse device step must not round above the ramp budget.
                value_w = value * rating / 100 if unit == "%" else value
                if value_w > baseline_w + positive_step_w + 0.001:
                    continue
            previous = self._last_sent.get(entity_id)
            if not emergency and not force and self._start_hold.active(entity_id, now):
                # Sustained negative signed-grid readings still permit reductions.
                exporting = self._grid_filtered_w is not None and self._grid_filtered_w < -120
                if not (exporting and previous is not None and value < previous):
                    continue
            # No-op writes on startup only create unnecessary communication
            # (and a fake pending acknowledgement) for already-correct limits.
            reported_value = self._float(state.state)
            pending = self._pending.get(entity_id)
            if (
                not force
                and emergency
                and pending is not None
                and abs(pending.target - value) < 0.01
                and now - pending.sent_at < FEEDBACK_TIMEOUT_SECONDS
            ):
                continue
            if (
                not force
                and previous is None
                and reported_value is not None
                and abs(reported_value - value) <= max(step / 2, 0.01)
            ):
                continue
            if previous is not None and not force:
                # A finely controlled percent actuator needs its 0.1pp
                # steps to *pass* the old 10 W deadband. The 20-second
                # increase interval and fresh Modbus/grid acknowledgements
                # still gate every step; other actuators keep their
                # existing anti-chatter deadband.
                fine_percent_increase = unit == "%" and 0 < step <= 0.1 and value > previous
                min_change = (
                    step / 2
                    if fine_percent_increase
                    else max(step, MIN_OUTPUT_CHANGE_W * (100 / rating if unit == "%" else 1))
                )
                if abs(value - previous) < min_change and not (
                    emergency and reported_value is not None and value < reported_value
                ):
                    continue
                elapsed = now - self._sent_at.get(entity_id, 0)
                # Safety fallback and deliberate shutdown may bypass the normal interval.
                if not (
                    emergency
                    and (
                        value < previous or (reported_value is not None and value < reported_value)
                    )
                ):
                    # After sustained high import, allow the same bounded
                    # 20 W steps at the device's normal interval. First start,
                    # actuator acknowledgements, and meter gating remain unchanged.
                    required = interval if value <= previous or high_import else max(interval, 20.0)
                    if elapsed < required:
                        continue
            reported_before = getattr(state, "last_reported", None) or state.last_updated
            _, grid_before = self._grid()
            last_grid = self._grid_filter.last_sequence
            if isinstance(last_grid, datetime) and (
                grid_before is None
                or (isinstance(grid_before, datetime) and last_grid > grid_before)
            ):
                grid_before = last_grid
            self._inflight_commands.add(entity_id)
            if not await self._save_shutdown_obligations() and not emergency:
                return
            try:
                await self.hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": entity_id, "value": value},
                    blocking=True,
                )
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if emergency and entity_id in self._shutdown_obligations:
                    self._shutdown_failed_at[entity_id] = now
                if entity_id in self._inflight_commands:
                    self._shutdown_obligations.add(entity_id)
                    self._deferred_shutdown.add(entity_id)
                if task is not None and task.cancelling():
                    raise
                self.last_error = f"{entity_id}: Solarman request was cancelled"
                self.status = "write_error"
                _LOGGER.warning("Inverter command cancelled: %s", entity_id)
                continue
            except (HomeAssistantError, TimeoutError, OSError, ValueError) as exc:
                if emergency and entity_id in self._shutdown_obligations:
                    self._shutdown_failed_at[entity_id] = now
                if entity_id in self._inflight_commands:
                    self._shutdown_obligations.add(entity_id)
                    self._deferred_shutdown.add(entity_id)
                self.last_error = f"{entity_id}: {exc}"
                self.status = "write_error"
                _LOGGER.warning("Unable to set %s limit: %s", entity_id, exc)
                continue
            self._start_hold.record(
                entity_id,
                before_w=(reported_value or 0) * rating / 100
                if unit == "%"
                else (reported_value or 0),
                after_w=target,
                now=now,
            )
            self._last_sent[entity_id] = value
            self._sent_at[entity_id] = now
            self._pending[entity_id] = PendingCommand(
                target=value,
                entity_reported_before=reported_before,
                sent_at=now,
                grid_reported_before=grid_before,
            )

    async def _verify_shutdown(self, now: float) -> None:
        """Confirm each zero-limit actuator report; never assume service success."""
        failures = []
        acknowledged = set()
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
                and await self._hardware_feedback_matches(entity_id, pending.target)
            ):
                acknowledged.add(entity_id)
            elif now - pending.sent_at >= FEEDBACK_TIMEOUT_SECONDS:
                failures.append(entity_id)
                if (
                    entity_id not in self._shutdown_retried
                    or now - pending.sent_at >= SHUTDOWN_RETRY_BACKOFF_SECONDS
                ):
                    self._shutdown_retried.add(entity_id)
                    self._shutdown_pending.pop(entity_id)
                    self._deferred_shutdown.add(entity_id)
        if acknowledged:
            remaining = self._shutdown_obligations - acknowledged
            old_inflight = self._inflight_commands.copy()
            self._inflight_commands.difference_update(acknowledged)
            if not await self._save_shutdown_obligations(remaining):
                self._inflight_commands = old_inflight
                return
            self._shutdown_obligations = remaining
            for entity_id in acknowledged:
                self._shutdown_pending.pop(entity_id, None)
                self._shutdown_retried.discard(entity_id)
                self._shutdown_failed_at.pop(entity_id, None)
            if (
                not remaining
                and self.last_error
                and self.last_error.startswith(
                    ("Shutdown not acknowledged:", "Cannot persist inverter shutdown obligations:")
                )
            ):
                self.last_error = None
        if self._deferred_shutdown:
            self.last_error = "Cannot stop unavailable inverters: " + ", ".join(
                sorted(self._deferred_shutdown)
            )
            self.status = "write_error"
        elif failures:
            self.last_error = "Shutdown not acknowledged: " + ", ".join(failures)
            self.status = "write_error"
        elif self.last_error is not None:
            self.status = "write_error"
        elif self._shutdown_pending:
            self.status = "shutdown_pending"
        else:
            self.status = "disabled"

    async def async_update(self) -> None:
        """One serialized update; never trust stale meter readings."""
        if not self._running or self._lock.locked():
            return
        async with self._lock:
            self.controller.feed_in = (
                bool(self.entry.options.get(CONF_FEED_IN, False))
                and self.entry.options.get(CONF_FEED_IN_MODE, FEED_IN_MODE_PV) == FEED_IN_MODE_PV
            )
            self.controller.adaptive_pid = bool(self.entry.options.get("adaptive_pid", False))
            self.controller.pid_gains = PIDGains(
                kp_min=float(self.entry.options.get("pid_kp_min", 0.5)),
                kp_max=float(self.entry.options.get("pid_kp_max", 0.9)),
                kp_export=float(self.entry.options.get("pid_kp_export", 1.0)),
                ki=float(self.entry.options.get("pid_ki", 0.02)),
                kd=float(self.entry.options.get("pid_kd", 0.12)),
            )
            self.controller.enabled = bool(self.entry.options.get(CONF_ENABLED, True))
            self.controller.full_hysteresis_pct = float(
                self.entry.options.get(CONF_FEED_IN_HYSTERESIS, 2.0)
            )
            batteries = self._batteries()
            reconnected = self._reconnected_inverters(batteries)
            if reconnected:
                for entity_id in reconnected:
                    self._shutdown_failed_at.pop(entity_id, None)
                # Reconnection is a new safety boundary even when a prior
                # disable was fully acknowledged and its journal is empty.
                # Persist before dispatch; a storage error must never stop
                # the best-effort emergency hardware command.
                self._shutdown_obligations.update(reconnected)
                self._deferred_shutdown.update(reconnected)
                await self._save_shutdown_obligations()
            if not self.controller.enabled or self._shutdown_obligations:
                if self._deferred_shutdown:
                    recovered = {
                        inverter.key: inverter.minimum_w
                        for battery in self._batteries()
                        for inverter in battery.inverters
                        if inverter.key in self._deferred_shutdown
                        and inverter.online
                        and monotonic() - self._shutdown_failed_at.get(inverter.key, float("-inf"))
                        >= SHUTDOWN_RETRY_BACKOFF_SECONDS
                    }
                    if recovered:
                        # A prior normal command must never be mistaken for the
                        # minimum command, including if this write now fails.
                        for entity_id in recovered:
                            self._pending.pop(entity_id, None)
                        await self._apply(recovered, monotonic(), emergency=True, force=True)
                        sent = recovered.keys() & self._pending.keys()
                        self._deferred_shutdown.difference_update(sent)
                        self._shutdown_pending.update(
                            {entity_id: self._pending.pop(entity_id) for entity_id in sent}
                        )
                        if (
                            not self._deferred_shutdown
                            and self.last_error
                            and self.last_error.startswith("Cannot stop unavailable inverters:")
                        ):
                            self.last_error = None
                await self._verify_shutdown(monotonic())
                if not self.controller.enabled:
                    self._publish()
                    return
            now = monotonic()
            self._start_hold.seconds = float(self.entry.options.get("start_hold_seconds", 30.0))
            self.controller.grid_target_w = float(
                self.entry.options.get("grid_target_w", DEFAULT_GRID_TARGET_W)
            )
            grid, sequence = self._grid()
            self._grid_raw_w = grid
            if grid is None or sequence is None:
                self._grid_filter.reset()
            else:
                grid = self._grid_filter.observe(grid, sequence)
            # The median delays sustained export after a sharp load drop.
            # Require two distinct significant export samples and retain the
            # less extreme one; isolated transients still use the median.
            if grid is not None and not self.controller.feed_in:
                confirmed_export = self._grid_filter.confirmed_export()
                if confirmed_export is not None:
                    grid = min(grid, confirmed_export)
            self._grid_filtered_w = grid
            batteries = tuple(
                replace(
                    battery,
                    inverters=tuple(
                        replace(inv, online=False) if inv.key in self._shutdown_obligations else inv
                        for inv in battery.inverters
                    ),
                )
                for battery in self._batteries()
            )
            if self._grid_raw_w is not None and sequence != self._last_raw_grid_sequence:
                # The 2026-11-04 hardware trace jumped directly from +153 W
                # to -696 W: do not require an earlier negative meter sample.
                # A single severe export during startup is an emergency,
                # bypassing the filter and the normal startup settling hold.
                self._startup_export_trip |= (
                    not self.controller.feed_in
                    and self._grid_raw_w < STARTUP_EXPORT_TRIP_W
                    and any(
                        self._start_hold.active(inv.key, now)
                        for battery in batteries
                        for inv in battery.inverters
                    )
                )
                self._last_raw_grid_sequence = sequence
            detected_trips = {
                battery.key
                for battery in batteries
                if battery.dc_output_w is not None and battery.dc_output_w > MAX_BK215_DC_W
            }
            if detected_trips - self._overload_latched:
                self._overload_latched.update(detected_trips)
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    options={
                        **self.entry.options,
                        CONF_DC_TRIPPED: sorted(self._overload_latched),
                    },
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
            if self._startup_export_trip:
                safe = {
                    inv.key: inv.minimum_w
                    for battery in batteries
                    for inv in battery.inverters
                    if inv.online
                }
                self.last_error = None
                await self._apply(safe, now, emergency=True)
                if self.last_error is None:
                    self.status = "write_error"
                    self.last_error = "Excess grid export during startup; switch EMS off to reset"
                self._publish()
                return
            missing_dc = sorted(
                self._dc_monitored[battery.key]
                for battery in batteries
                if battery.key in self._dc_monitored and battery.dc_output_w is None
            )
            if missing_dc:
                safe = {
                    inv.key: inv.minimum_w
                    for battery in batteries
                    for inv in battery.inverters
                    if inv.online
                }
                self.last_error = None
                await self._apply(safe, now, emergency=True)
                if self.last_error is None:
                    self.status = "dc_unavailable"
                    self.last_error = "BK215 DC sensor not current: " + ", ".join(missing_dc)
                self._publish()
                return
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

            if not self._seen_valid:
                if grid is None or not any(
                    battery.soc is not None
                    and battery.lower_soc is not None
                    and any(inverter.online for inverter in battery.inverters)
                    for battery in batteries
                ):
                    self.status = "write_error" if self._shutdown_obligations else "waiting"
                    self._publish()
                    return
                self._seen_valid = True
            if grid is None:
                await self._feedback_ready(now, None)
                feedback_error = self.status == "write_error"
                outputs = self.controller.update(batteries, grid, now, sequence=sequence)
                if not feedback_error:
                    self.status = "grid_unavailable"
                await self._apply(outputs, now, emergency=True)
                self._publish()
                return

            last_grid = self._grid_filter.last_sequence
            stale_grid = (
                isinstance(sequence, datetime)
                and isinstance(last_grid, datetime)
                and sequence < last_grid
            )
            # Check pending commands even when the meter goes backwards, so
            # missing device feedback can still trigger the safety timeout.
            if not await self._feedback_ready(now, None if stale_grid else sequence):
                self._publish()
                return
            if stale_grid:
                self.status = "waiting_meter"
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
            # A held inverter must not silently accumulate PID integral
            # while normal actuator writes are intentionally postponed.
            self.controller.pid_hold = any(
                inverter.online and self._start_hold.active(inverter.key, now)
                for battery in batteries
                for inverter in battery.inverters
            )
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
            if self._shutdown_obligations:
                self.status = "write_error"
                self.last_error = "Inverter safe minimum not confirmed: " + ", ".join(
                    sorted(self._shutdown_obligations)
                )
            # Show requested limits only when actually sent. If a write was
            # rate-limited, keep the confirmed baseline in public diagnostics.
            if not self._pending:
                self.controller.state.outputs = self._reported_outputs(batteries)
            self._publish()

    async def async_disable(self) -> None:
        """One-shot safe command when the user deliberately disables EMS."""
        async with self._lock:
            self.controller.enabled = False
            self._grid_filter.reset()
            self._start_hold.reset()
            self._startup_export_trip = False
            self._last_raw_grid_sequence = None
            self.controller.pid_hold = False
            self.last_error = None
            self._pending.clear()
            self._shutdown_pending.clear()
            self._shutdown_retried.clear()
            commands = self.controller.disable(self._batteries())
            configured = self._configured_inverters()
            unreachable = sorted(configured - commands.keys())
            self._shutdown_obligations.update(configured)
            self._deferred_shutdown.update(configured)
            persisted = await self._save_shutdown_obligations()
            persistence_error = self.last_error if not persisted else None
            await self._apply(commands, monotonic(), emergency=True, force=True)
            self._shutdown_pending = dict(self._pending)
            self._deferred_shutdown.difference_update(self._shutdown_pending)
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
            if persistence_error:
                self.last_error = (
                    f"{persistence_error}; {self.last_error}"
                    if self.last_error and self.last_error != persistence_error
                    else persistence_error
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
        if not enabled:
            # A deliberate reset in Configure is permitted only while EMS is off.
            self._overload_latched = set(self.entry.options.get(CONF_DC_TRIPPED, ()))
        self.controller.feed_in = (
            bool(self.entry.options.get(CONF_FEED_IN, False))
            and self.entry.options.get(CONF_FEED_IN_MODE, FEED_IN_MODE_PV) == FEED_IN_MODE_PV
        )
        adaptive = bool(self.entry.options.get("adaptive_pid", False))
        if adaptive != self.controller.adaptive_pid:
            self.controller.adaptive_pid = adaptive
            self.controller._reset_pid()
        self._start_hold.seconds = float(self.entry.options.get("start_hold_seconds", 30.0))
        if not enabled and self.controller.enabled:
            await self.async_disable()
        elif enabled and not self.controller.enabled:
            self.controller.enabled = True
            self._grid_filter.reset()
            self._seen_valid = False
            self.status = "waiting"
            await self.async_update()
        self._publish()
