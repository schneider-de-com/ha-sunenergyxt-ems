"""Historical point forecasts, separate from live planning eligibility."""

import asyncio
from collections.abc import Callable, Mapping
from copy import deepcopy
from datetime import datetime, timedelta
from math import isfinite
from typing import Any, cast

from homeassistant.core import CoreState, HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import DOMAIN
from .forecast import ForecastError, _utc

_FIELDS = {"config_entry_id", "received_at", "configuration_updated_at", "periods"}


def _stamp(value: object) -> datetime:
    if not isinstance(value, str):
        raise ForecastError("invalid_forecast_evidence")
    return cast(datetime, _utc(datetime.fromisoformat(value)))


def _snapshot(value: object, now: datetime) -> dict[str, Any]:
    """Whitelist only provenance and energy intervals; never arbitrary owner data."""
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ForecastError("invalid_forecast_evidence")
    owner = value["config_entry_id"]
    receipt = _stamp(value["received_at"])
    revision = value["configuration_updated_at"]
    if not isinstance(owner, str) or not 0 < len(owner) <= 100 or receipt > now:
        raise ForecastError("invalid_forecast_evidence")
    if revision is not None and _stamp(revision) > receipt:
        raise ForecastError("invalid_forecast_evidence")
    periods = value["periods"]
    if not isinstance(periods, list) or not 0 < len(periods) <= 200:
        raise ForecastError("invalid_forecast_evidence")
    previous: datetime | None = None
    normalized = []
    for period in periods:
        if not isinstance(period, list) or len(period) != 3:
            raise ForecastError("invalid_forecast_evidence")
        start, end = _stamp(period[0]), _stamp(period[1])
        wh = period[2]
        if (
            not timedelta() < end - start <= timedelta(days=2)
            or (previous is not None and start < previous)
            or not receipt - timedelta(days=2) <= start < end <= receipt + timedelta(days=3)
            or isinstance(wh, bool)
            or not isinstance(wh, (int, float))
            or not isfinite(wh)
            or not 0 <= wh <= 100_000
        ):
            raise ForecastError("invalid_forecast_evidence")
        previous = end
        normalized.append([start.isoformat(), end.isoformat(), float(wh)])
    return {
        "config_entry_id": owner,
        "received_at": receipt.isoformat(),
        "configuration_updated_at": _stamp(revision).isoformat() if revision is not None else None,
        "periods": normalized,
    }


class ForecastEvidence:
    """Own a separate bounded HA Store; never restore curves into a live source.

    The separate key also survives an experimental rollback to b12, whose demand
    writer knows nothing about this ledger. Save/readback errors remain explicit;
    corrupt evidence is not silently replaced. No new timer or polling is added.
    """

    def __init__(self, hass: HomeAssistant, entry_id: str, changed: Callable[[], None]) -> None:
        self.hass, self.changed = hass, changed
        self._store: Store[dict[str, Any]] = Store(
            hass, 1, f"{DOMAIN}.forecast_evidence.{entry_id}", atomic_writes=True
        )
        self._snapshots: list[dict[str, Any]] = []
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task[None]] = set()
        self._loaded = False
        self._closed = False
        self.persistence_ok = False
        self.storage_error: str | None = None

    async def async_start(self) -> None:
        try:
            saved = await self._store.async_load()
            if (
                saved is None
                and isinstance(self.hass, HomeAssistant)
                and any(
                    domain == "homeassistant"
                    and key.startswith(f"storage_corruption_{self._store.key}_")
                    for domain, key in ir.async_get(self.hass).issues
                )
            ):
                raise ForecastError("corrupt_forecast_evidence")
            if saved is not None:
                if (
                    not isinstance(saved, Mapping)
                    or type(saved.get("version")) is not int
                    or saved.get("version") != 1
                    or not isinstance(saved.get("snapshots"), list)
                    or len(saved["snapshots"]) > 2048
                ):
                    raise ForecastError("invalid_forecast_evidence_store")
                now = dt_util.utcnow()
                rows = [_snapshot(row, now) for row in saved["snapshots"]]
                keys = [(row["config_entry_id"], row["received_at"]) for row in rows]
                if len(set(keys)) != len(keys):
                    raise ForecastError("duplicate_forecast_evidence")
                self._snapshots = [
                    row for row in rows if _stamp(row["received_at"]) >= now - timedelta(days=14)
                ]
            self._loaded = self.persistence_ok = True
        except Exception:
            self.storage_error = "forecast_evidence_restore_failed"

    def submit(self, snapshot: dict[str, Any]) -> None:
        """A source calls only after an observed successful owner/cache update."""
        if self._closed:
            return
        task = self.hass.async_create_task(self.async_record(deepcopy(snapshot)))
        self._tasks.add(task)

        task.add_done_callback(self._tasks.discard)

    async def async_record(self, snapshot: object) -> None:
        async with self._lock:
            if not self._loaded:
                return
            try:
                now = dt_util.utcnow()
                clean = _snapshot(snapshot, now)
                cutoff = now - timedelta(days=14)
                rows = [row for row in self._snapshots if _stamp(row["received_at"]) >= cutoff]
                key = (clean["config_entry_id"], clean["received_at"])
                matching = [
                    row for row in rows if (row["config_entry_id"], row["received_at"]) == key
                ]
                if matching and matching != [clean]:
                    raise ForecastError("conflicting_forecast_evidence")
                if not matching and _stamp(clean["received_at"]) >= cutoff:
                    rows.append(clean)
                rows.sort(key=lambda row: _stamp(row["received_at"]))
                rows = rows[-2048:]
                payload = {"version": 1, "snapshots": rows}
                if rows != self._snapshots or not self.persistence_ok:
                    # A failed/uncertain write must not make the next successful
                    # write drop this observed receipt. It stays explicitly
                    # unconfirmed until save and independent readback succeed.
                    self._snapshots = rows
                    await self._store.async_save(payload)
                    if (
                        getattr(self.hass, "state", None) is CoreState.stopping
                        or await self._store.async_load() != payload
                    ):
                        raise ForecastError("forecast_evidence_readback_mismatch")
                self._snapshots = rows
                self.persistence_ok, self.storage_error = True, None
            except Exception:
                self.persistence_ok = False
                self.storage_error = "forecast_evidence_write_failed"
            self.changed()

    def summary(self) -> dict[str, object]:
        """Expose bounded bookkeeping, never household curves or owner secrets."""
        receipts = [row["received_at"] for row in self._snapshots]
        return {
            "semantics": "historical_point_forecasts_not_live_eligibility",
            "snapshot_count": len(receipts),
            "latest_received_at": max(receipts, key=_stamp) if receipts else None,
            "persistence_ok": self.persistence_ok,
            "storage_error": self.storage_error,
            "may_include_unconfirmed": not self.persistence_ok,
            "provider_issued_at": None,
            "uncertainty": "not_supplied",
            "energy_stage": "provider_estimate_not_verified_bk_dc_input",
            "validation": "parsed_raw_intervals_not_daily_reconciled_or_calibrated",
        }

    async def async_stop(self) -> None:
        self._closed = True
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks))
