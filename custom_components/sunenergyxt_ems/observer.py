"""Read-only HA demand collection, separate from the hardware control loop."""

import asyncio
import json
import logging
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import partial

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CoreState, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .demand_history import DemandHistory
from .forecast import ForecastError, _utc
from .operating_history import OperatingHistory, OperatingSample

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HouseholdObservation:
    """A complete household observation, or an explicit quality failure."""

    at: datetime | None
    watts: float | None
    reason: str


class DemandObserver:
    """Own cached-state polling and storage; never call a hardware service."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, read: Callable[[], HouseholdObservation]
    ) -> None:
        self.hass, self.entry, self.read = hass, entry, read
        self.history = DemandHistory()
        self.operating = OperatingHistory()
        self.read_operating: Callable[[datetime], OperatingSample] | None = None
        self.last_poll_at: datetime | None = None
        self.storage_error: str | None = None
        self.persistence_ok = False
        self.reason = "observer_start"
        self._events: list[dict[str, str]] = []
        self._source_key = self._sources()
        self._store: Store[dict[str, object]] = Store(
            hass, 1, f"{DOMAIN}.demand.{entry.entry_id}", atomic_writes=True
        )
        self._lock = asyncio.Lock()
        self._running = False
        self._loaded = False
        self._cancel: Callable[[], None] | None = None
        self._last_save_at: datetime | None = None
        self._last_measurement: datetime | None = None
        self._restart_gap = False
        self._source_changed_on_restore = False

    @property
    def events(self) -> tuple[dict[str, str], ...]:
        """Quality transitions, not proof of continuous physical regulation."""
        return tuple(deepcopy(self._events))

    def _sources(self) -> str:
        inverters = sorted(
            (inv["entity_id"], self.entry.options.get("ac_power", {}).get(inv["entity_id"]))
            for battery in self.entry.data.get("batteries", [])
            for inv in battery.get("inverters", [])
        )
        return json.dumps(
            [self.entry.data.get("grid"), self.entry.data.get("invert_grid", False), inverters]
        )

    def _event(self, at: datetime, reason: str) -> None:
        if (
            not self._events
            or self._events[-1]["reason"] != reason
            or self._events[-1]["source_key"] != self._source_key
        ):
            self._events.append(
                {"at": at.isoformat(), "reason": reason, "source_key": self._source_key}
            )
        # Keep one preceding anchor as well as the full two-week event window.
        cutoff = (at - timedelta(days=14)).isoformat()
        while len(self._events) > 1 and self._events[1]["at"] < cutoff:
            self._events.pop(0)

    async def async_start(self) -> None:
        """Restore before collecting and install at most one passive callback."""
        if self._running:
            return
        self._running = True
        self._cancel = async_track_time_interval(
            self.hass, self.async_refresh, timedelta(seconds=5)
        )
        await self.async_refresh(dt_util.utcnow())

    async def async_stop(self) -> None:
        """Stop callbacks before waiting for a final storage checkpoint."""
        self._running = False
        if self._cancel is not None:
            self._cancel()
            self._cancel = None
        async with self._lock:
            if self._loaded:
                await self._save()

    async def _restore(self, now: datetime) -> None:
        saved = await self._store.async_load()
        # Core can rename corrupt JSON, create a persistent Repair and return
        # None. That is lost evidence, not a clean first installation.
        if saved is None and any(
            domain == "homeassistant"
            and issue_id.startswith(f"storage_corruption_{self._store.key}_")
            for domain, issue_id in ir.async_get(self.hass).issues
        ):
            raise ForecastError("observer_storage_corruption")
        if saved is not None:
            if (
                not isinstance(saved, Mapping)
                or type(saved.get("version")) is not int
                or saved.get("version") != 1
                or not isinstance(saved.get("source_key"), str)
            ):
                raise ForecastError("invalid_observer_store")
            history = DemandHistory.from_dict(saved.get("history"), now=now)
            operating = (
                await self.hass.async_add_executor_job(
                    partial(OperatingHistory.from_dict, saved["operating_history"], now=now)
                )
                if "operating_history" in saved
                else OperatingHistory()
            )
            raw_events, raw_poll = saved.get("events"), saved.get("last_poll_at")
            if not isinstance(raw_events, list) or not isinstance(raw_poll, str):
                raise ForecastError("invalid_observer_store")
            events: list[dict[str, str]] = []
            previous: datetime | None = None
            for event in raw_events:
                if not isinstance(event, dict) or any(
                    not isinstance(event.get(key), str) for key in ("at", "reason", "source_key")
                ):
                    raise ForecastError("invalid_observer_store")
                stamp = _utc(datetime.fromisoformat(event["at"]))
                if stamp > now or (previous is not None and stamp < previous):
                    raise ForecastError("invalid_observer_store")
                events.append(
                    {
                        "at": stamp.isoformat(),
                        "reason": event["reason"],
                        "source_key": event["source_key"],
                    }
                )
                previous = stamp
            poll = _utc(datetime.fromisoformat(raw_poll))
            if (
                poll > now
                or (previous is not None and poll < previous)
                or (history.intervals and history.intervals[-1].end > poll)
            ):
                raise ForecastError("invalid_observer_store")
            self._events, self.last_poll_at = events, poll
            self.operating = operating
            self._restart_gap = now > poll
            if saved["source_key"] == self._source_key:
                self.history = history
            else:
                self._source_changed_on_restore = True
        self._loaded = True

    async def _save(self) -> None:
        payload: dict[str, object] = {
            "version": 1,
            "source_key": self._source_key,
            "history": self.history.as_dict(),
            # The observer lock prevents mutation while the worker snapshots
            # up to two weeks of minute rows. Hardware control keeps running.
            "operating_history": await self.hass.async_add_executor_job(self.operating.as_dict),
            "events": deepcopy(self._events),
            "last_poll_at": self.last_poll_at.isoformat() if self.last_poll_at else None,
        }
        try:
            await self._store.async_save(payload)
            if (
                getattr(self.hass, "state", None) is CoreState.stopping
                or await self._store.async_load() != payload
            ):
                raise OSError("demand checkpoint readback failed")
        except (OSError, HomeAssistantError) as exc:
            self.persistence_ok = False
            self.storage_error = str(exc)
            _LOGGER.warning("Cannot persist demand observations: %s", exc)
        else:
            self.persistence_ok, self.storage_error = True, None
        self._last_save_at = self.last_poll_at

    async def async_refresh(self, now: datetime) -> None:
        """Collect independently of feedback/shutdown gates in the writer."""
        async with self._lock:
            if not self._running:
                return
            # A timer can wait behind a checkpoint worker. Its scheduled time
            # then predates the states we are about to read. Refresh that time
            # under the lock; a real wall-clock reversal still stays reversed.
            now = max(_utc(now), _utc(dt_util.utcnow()))
            if not self._loaded:
                try:
                    await self._restore(now)
                except (OSError, HomeAssistantError, ForecastError, ValueError) as exc:
                    self.storage_error = str(exc)
                    self.persistence_ok = False
                    self.reason = "storage_unavailable"
                    return  # Do not overwrite unreadable existing evidence.
            now = max(now, _utc(dt_util.utcnow()))
            if self.last_poll_at is not None and now < self.last_poll_at:
                self.history = DemandHistory()
                self.operating.break_coverage()
                self.reason = "observer_clock_reversed"
                self.persistence_ok = False
                return
            if self.last_poll_at is not None and (
                now - self.last_poll_at > timedelta(seconds=90) or self._restart_gap
            ):
                self._event(self.last_poll_at, "observer_gap")
                self.history.break_coverage()
            self._restart_gap = False
            if self._source_changed_on_restore:
                self._event(now, "source_changed")
                self._source_changed_on_restore = False
            source_key = self._sources()
            if source_key != self._source_key:
                self._source_key = source_key
                self.history = DemandHistory()
                self.operating.break_coverage()
                self._last_measurement = None
                self._event(now, "source_changed")
            try:
                observation = self.read()
            except (HomeAssistantError, TypeError, ValueError, OverflowError) as exc:
                _LOGGER.debug("Cannot read household observation: %s", exc)
                observation = HouseholdObservation(None, None, "observation_error")
            self.reason = observation.reason
            try:
                if observation.at is not None and _utc(observation.at) > now:
                    self.reason = "source_time_ahead"
                    self.history.observe(now, None)
                elif observation.watts is None or observation.at is None:
                    self.history.observe(now, None)
                elif observation.at != self._last_measurement:
                    self.history.observe(observation.at, observation.watts)
                    self._last_measurement = observation.at
            except ForecastError:
                self.reason = "invalid_measurement_sequence"
            self._event(now, self.reason)
            if self.read_operating is not None:
                try:
                    operating = self.read_operating(now)
                    if _utc(operating.at) != now:
                        raise ForecastError("operating_observation_time_mismatch")
                    self.operating.observe(operating)
                except (
                    HomeAssistantError,
                    ForecastError,
                    TypeError,
                    ValueError,
                    OverflowError,
                ) as exc:
                    self.operating.break_coverage()
                    self.reason = "operating_observation_error"
                    self._event(now, self.reason)
                    _LOGGER.warning("Cannot capture operating evidence: %s", exc)
            self.last_poll_at = now
            if self._last_save_at is None or now - self._last_save_at >= timedelta(minutes=1):
                await self._save()
