"""Read Forecast.Solar's owner cache; never request provider data or invent confidence."""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, time, timedelta, tzinfo
from itertools import pairwise
from math import isclose, isfinite
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .forecast import EnergyInterval, ForecastError, _utc, period_energy_forecast


@dataclass(frozen=True, slots=True)
class PointSolarForecast:
    """Local successful-receipt provenance, with no provider confidence claim."""

    expected: tuple[EnergyInterval, ...]
    received_at: datetime
    source_entry_id: str
    source_entities: tuple[str, str]
    provider_issued_at: None = None
    lower: None = None
    upper: None = None
    uncertainty: str = "not_supplied"


def _series(value: object) -> dict[datetime, float]:
    if not isinstance(value, Mapping) or not value:
        raise ForecastError("invalid_forecast_solar_series")
    result: dict[datetime, float] = {}
    for key, number in value.items():
        if not isinstance(key, str):
            raise ForecastError("invalid_forecast_solar_time")
        try:
            stamp = _utc(datetime.fromisoformat(key))
        except ValueError as exc:
            raise ForecastError("invalid_forecast_solar_time") from exc
        if (
            stamp in result
            or isinstance(number, bool)
            or not isinstance(number, (int, float))
            or not isfinite(number)
            or not 0 <= number <= 100_000
        ):
            raise ForecastError("invalid_forecast_solar_value")
        result[stamp] = float(number)
    return result


def _periods(response: object) -> tuple[EnergyInterval, ...]:
    if not isinstance(response, Mapping):
        raise ForecastError("invalid_forecast_solar_response")
    # HA 2026.11 raw service uses START-keyed Wh. Watts supply endpoints only:
    # provider and HA disagree about their power-sample semantics.
    points = sorted(_series(response.get("watts")))
    energy = _series(response.get("wh_period"))
    if len(points) < 2 or set(energy) != set(points[:-1]):
        raise ForecastError("incomplete_forecast_solar_periods")
    return tuple(EnergyInterval(start, end, energy[start]) for start, end in pairwise(points))


def _fingerprint(entry: Any) -> object:
    return deepcopy(
        (
            dict(entry.data),
            dict(entry.options),
            tuple((key, dict(sub.data)) for key, sub in sorted(entry.subentries.items())),
        )
    )


class ForecastSolarSource:
    """Observe genuine future owner updates, with cancellation and fail-closed age."""

    def __init__(self, hass: HomeAssistant, changed: Callable[[], None]) -> None:
        self.hass, self.changed = hass, changed
        self.entry_id: str | None = None
        self._coordinator: DataUpdateCoordinator[Any] | None = None
        self._identity: object = None
        self._seen: object = None
        self._fingerprint: object = None
        self._unsubscribe: Callable[[], None] | None = None
        self._generation = 0
        self._closed = False
        self._tasks: set[asyncio.Task[None]] = set()
        self.task: asyncio.Task[None] | None = None
        self._periods: tuple[EnergyInterval, ...] = ()
        self._received_at: datetime | None = None
        self._timezone: tzinfo | None = None
        self.reason = "forecast_solar_receipt_unknown"

    def _clear(self, reason: str) -> None:
        self._generation += 1
        if self.task is not None:
            self.task.cancel()
        self._periods, self._received_at, self._timezone = (), None, None
        self.reason = reason

    def bind(self, entry_id: str | None) -> None:
        """An existing cache is intentionally not timestamped as newly received."""
        if self._closed:
            return
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        coordinator = getattr(entry, "runtime_data", None)
        valid = (
            entry is not None
            and entry.domain == "forecast_solar"
            and entry.state is ConfigEntryState.LOADED
            and isinstance(coordinator, DataUpdateCoordinator)
        )
        fingerprint = _fingerprint(entry) if valid else None
        owner = coordinator if valid else None
        if (
            owner is self._coordinator
            and fingerprint == self._fingerprint
            and entry_id == self.entry_id
        ):
            return
        self._clear("forecast_solar_receipt_unknown" if valid else "forecast_solar_unavailable")
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        self.entry_id, self._coordinator, self._fingerprint = entry_id, owner, fingerprint
        self._seen = owner.data if owner is not None else None
        if owner is not None:
            self._unsubscribe = owner.async_add_listener(self._updated)

    def _current(self) -> bool:
        entry = self.hass.config_entries.async_get_entry(self.entry_id) if self.entry_id else None
        return bool(
            not self._closed
            and self._coordinator is not None
            and self._coordinator.last_update_success
            and entry is not None
            and entry.state is ConfigEntryState.LOADED
            and getattr(entry, "runtime_data", None) is self._coordinator
            and _fingerprint(entry) == self._fingerprint
        )

    def _updated(self) -> None:
        if not self._current():
            self._clear("forecast_solar_unavailable")
            self.changed()
            return
        coordinator = self._coordinator
        if coordinator is None:
            return
        data = coordinator.data
        if data is self._seen:
            return  # Listener replay is not another successful provider response.
        self._seen = data
        self._clear("forecast_solar_receipt_unknown")
        generation = self._generation
        at = dt_util.utcnow()
        self.task = self.hass.async_create_task(self._async_read(generation, data, at))
        self._tasks.add(self.task)
        self.task.add_done_callback(self._tasks.discard)
        self.changed()

    async def _async_read(self, generation: int, identity: object, at: datetime) -> None:
        try:
            if not self.hass.services.has_service("forecast_solar", "get_forecast"):
                raise ForecastError("forecast_solar_service_unavailable")
            async with asyncio.timeout(10):
                response = await self.hass.services.async_call(
                    "forecast_solar",
                    "get_forecast",
                    {"config_entry": self.entry_id, "resolution": "raw"},
                    blocking=True,
                    return_response=True,
                )
                periods = _periods(response)
                zone = getattr(identity, "timezone", None)
                timezone = (
                    await dt_util.async_get_time_zone(zone) if isinstance(zone, str) else None
                )
                if timezone is None:
                    raise ForecastError("forecast_solar_timezone_missing")
            if (
                generation != self._generation
                or not self._current()
                or identity is not self._seen
                or self._coordinator is None
                or self._coordinator.data is not identity
            ):
                return
            self._periods, self._received_at, self._timezone = periods, at, timezone
            self._identity = identity
            self.reason = "cached_point_forecast"
        except asyncio.CancelledError:
            return
        except Exception:
            if generation != self._generation:
                return
            self._periods, self._received_at, self._timezone = (), None, None
            self.reason = "forecast_solar_read_failed"
        self.changed()

    def forecast(
        self, *, now: datetime, required_until: datetime, source_entities: tuple[str, str]
    ) -> PointSolarForecast:
        """Never equate a cache read, sensor report or restart with provider issuance."""
        if not self._current():
            raise ForecastError("forecast_solar_unavailable")
        if self._received_at is None or not self._periods or self._timezone is None:
            raise ForecastError(self.reason)
        age = _utc(now) - _utc(self._received_at)
        if not timedelta(minutes=-5) <= age <= timedelta(hours=2):
            raise ForecastError("forecast_solar_receipt_stale")
        coordinator = self._coordinator
        if coordinator is None or coordinator.data is not self._identity:
            raise ForecastError("forecast_solar_receipt_unknown")
        today = now.astimezone(self._timezone).date()
        for offset, entity in enumerate(source_entities):
            day = today + timedelta(days=offset)
            start = datetime.combine(day, time(), self._timezone)
            end = datetime.combine(day + timedelta(days=1), time(), self._timezone)
            total = sum(
                period.energy_wh
                * ((min(end, period.end) - max(start, period.start)) / (period.end - period.start))
                for period in self._periods
                if min(end, period.end) > max(start, period.start)
            )
            state = self.hass.states.get(entity)
            try:
                expected = float(state.state) if state else float("nan")
            except ValueError, TypeError:
                expected = float("nan")
            unit = state.attributes.get("unit_of_measurement") if state else None
            expected *= 1000 if unit == "kWh" else 1
            if (
                unit not in ("Wh", "kWh")
                or not isfinite(expected)
                or not isclose(total, expected, rel_tol=1e-9, abs_tol=1e-6)
            ):
                raise ForecastError("forecast_solar_daily_energy_mismatch")
        return PointSolarForecast(
            period_energy_forecast(self._periods, now=now, required_until=required_until),
            self._received_at,
            self.entry_id or "",
            source_entities,
        )

    def describe(self, at: datetime) -> dict[str, object]:
        reason = self.reason
        if not self._current():
            reason = "forecast_solar_unavailable"
        elif self._received_at is not None and not timedelta(minutes=-5) <= (
            _utc(at) - _utc(self._received_at)
        ) <= timedelta(hours=2):
            reason = "forecast_solar_receipt_stale"
        return {
            "config_entry_id": self.entry_id,
            "reason": reason,
            "received_at": self._received_at.isoformat() if self._received_at else None,
            "receipt_semantics": "observed_successful_ha_coordinator_update_not_provider_issuance",
            "provider_issued_at": None,
            "uncertainty": "not_supplied",
            "interval_count": len(self._periods),
            "period_energy_unit": "Wh",
            "energy_stage": "provider_estimate_not_verified_bk_dc_input",
        }

    async def async_stop(self) -> None:
        self._closed = True
        self._clear("forecast_solar_unavailable")
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
