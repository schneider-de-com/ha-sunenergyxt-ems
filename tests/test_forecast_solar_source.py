"""Observe owner updates; cache reads never create freshness or confidence."""

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from homeassistant.config_entries import ConfigEntryState  # noqa: E402
from homeassistant.core import State  # noqa: E402
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator  # noqa: E402

from custom_components.sunenergyxt_ems import forecast_solar_source as module  # noqa: E402
from custom_components.sunenergyxt_ems.forecast import ForecastError  # noqa: E402
from custom_components.sunenergyxt_ems.forecast_solar_source import (  # noqa: E402
    ForecastSolarSource,
)

NOW = datetime(2026, 11, 13, 12, tzinfo=UTC)


def payload():
    times = [NOW - timedelta(hours=1), NOW, NOW + timedelta(hours=1), NOW + timedelta(days=1)]
    return {
        "watts": {at.isoformat(): 9000 for at in times},
        "wh_period": {
            at.isoformat(): value for at, value in zip(times[:-1], (100, 200, 0), strict=True)
        },
    }


class Coordinator(DataUpdateCoordinator):
    def __init__(self):
        self.data = SimpleNamespace(timezone="UTC")
        self.last_update_success = True
        self.listeners = []

    def async_add_listener(self, listener, context=None):
        self.listeners.append(listener)
        return lambda: self.listeners.remove(listener)

    def update(self, *, success=True, replace=True):
        self.last_update_success = success
        if replace:
            self.data = SimpleNamespace(timezone="UTC")
        for listener in list(self.listeners):
            listener()


@pytest.fixture
def source_setup(monkeypatch):
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: NOW)
    coordinator = Coordinator()
    entry = SimpleNamespace(
        domain="forecast_solar",
        state=ConfigEntryState.LOADED,
        runtime_data=coordinator,
        data={},
        options={},
        subentries={},
    )
    states = {
        "sensor.today": State("sensor.today", "0.3", {"unit_of_measurement": "kWh"}),
        "sensor.tomorrow": State("sensor.tomorrow", "0", {"unit_of_measurement": "Wh"}),
    }
    service = AsyncMock(return_value=payload())
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_get_entry=lambda key: entry if key == "source" else None
        ),
        services=SimpleNamespace(has_service=lambda *args: True, async_call=service),
        states=SimpleNamespace(get=states.get),
        async_create_task=lambda coro, **kwargs: asyncio.create_task(coro),
    )
    changed = []
    source = ForecastSolarSource(hass, lambda: changed.append(True))
    return source, coordinator, entry, service, states


async def settle(source):
    await asyncio.sleep(0)
    if source.task is not None:
        await source.task


def read(source, *, now=NOW):
    return source.forecast(
        now=now,
        required_until=NOW + timedelta(hours=2),
        source_entities=("sensor.today", "sensor.tomorrow"),
    )


@pytest.mark.asyncio
async def test_attach_does_not_rejuvenate_existing_cache_or_poll_provider(source_setup):
    source, coordinator, _, service, _ = source_setup
    source.bind("source")
    assert len(coordinator.listeners) == 1
    service.assert_not_called()
    with pytest.raises(ForecastError, match="receipt_unknown"):
        read(source)
    coordinator.update(replace=False)
    service.assert_not_called()
    await source.async_stop()
    assert not coordinator.listeners


@pytest.mark.asyncio
async def test_real_success_notification_reads_only_cached_wh_and_keeps_uncertainty_unknown(
    source_setup,
):
    source, coordinator, _, service, _ = source_setup
    source.bind("source")
    coordinator.update()
    await settle(source)
    result = read(source)
    assert [r.energy_wh for r in result.expected] == [100, 100, 0, 0]
    assert result.received_at == NOW
    assert result.provider_issued_at is None
    assert result.lower is None and result.upper is None
    assert result.uncertainty == "not_supplied"
    service.assert_awaited_once_with(
        "forecast_solar",
        "get_forecast",
        {"config_entry": "source", "resolution": "raw"},
        blocking=True,
        return_response=True,
    )
    await source.async_stop()


@pytest.mark.asyncio
async def test_failure_and_old_receipt_cannot_authorize_the_point_forecast(source_setup):
    source, coordinator, _, _, _ = source_setup
    source.bind("source")
    coordinator.update()
    await settle(source)
    with pytest.raises(ForecastError, match="stale"):
        read(source, now=NOW + timedelta(hours=3))
    coordinator.update(success=False, replace=False)
    with pytest.raises(ForecastError, match="unavailable"):
        read(source)
    coordinator.update(replace=False)
    with pytest.raises(ForecastError):
        read(source)
    await source.async_stop()


@pytest.mark.parametrize("change", ["reload", "geometry", "remove"])
@pytest.mark.asyncio
async def test_source_identity_or_configuration_change_invalidates_receipt(source_setup, change):
    source, coordinator, entry, _, _ = source_setup
    source.bind("source")
    coordinator.update()
    await settle(source)
    assert read(source).expected
    if change == "reload":
        entry.runtime_data = Coordinator()
    elif change == "geometry":
        entry.options = {"azimuth": 134}
    else:
        entry.state = ConfigEntryState.NOT_LOADED
    source.bind("source")
    with pytest.raises(ForecastError):
        read(source)
    assert len(coordinator.listeners) == (1 if change == "geometry" else 0)
    await source.async_stop()


@pytest.mark.parametrize(
    "bad", ["missing_energy", "end_keyed_energy", "naive_time", "negative", "no_endpoint"]
)
@pytest.mark.asyncio
async def test_malformed_service_payload_is_not_turned_into_zeroes(source_setup, bad):
    source, coordinator, _, service, _ = source_setup
    response = payload()
    if bad == "missing_energy":
        response["wh_period"].pop(NOW.isoformat())
    elif bad == "end_keyed_energy":
        response["wh_period"] = {at: 100 for at in list(response["watts"])[1:]}
    elif bad == "naive_time":
        response["watts"][NOW.replace(tzinfo=None).isoformat()] = 0
    elif bad == "negative":
        response["wh_period"][NOW.isoformat()] = -1
    else:
        response["watts"].pop(list(response["watts"])[-1])
    service.return_value = response
    source.bind("source")
    coordinator.update()
    await settle(source)
    with pytest.raises(ForecastError):
        read(source)
    await source.async_stop()


@pytest.mark.asyncio
async def test_daily_energy_mismatch_is_not_silently_accepted(source_setup):
    source, coordinator, _, _, states = source_setup
    source.bind("source")
    coordinator.update()
    await settle(source)
    states["sensor.today"] = State("sensor.today", "0.4", {"unit_of_measurement": "kWh"})
    with pytest.raises(ForecastError, match="daily_energy"):
        read(source)
    await source.async_stop()


@pytest.mark.asyncio
async def test_unload_cancels_pending_cache_read_and_blocks_late_delivery(source_setup):
    source, coordinator, _, service, _ = source_setup
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed(*args, **kwargs):
        started.set()
        await release.wait()
        return payload()

    service.side_effect = delayed
    source.bind("source")
    coordinator.update()
    await started.wait()
    await source.async_stop()
    release.set()
    assert not coordinator.listeners
    assert source.describe(NOW)["received_at"] is None


@pytest.mark.asyncio
async def test_superseded_cache_read_cannot_replace_a_newer_owner_update(source_setup):
    source, coordinator, _, service, _ = source_setup
    started, release = asyncio.Event(), asyncio.Event()
    count = 0

    async def delayed_first(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 1:
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return {"watts": {}, "wh_period": {}}  # Late, invalid response must be ignored.
        return payload()

    service.side_effect = delayed_first
    source.bind("source")
    coordinator.update()
    await started.wait()
    coordinator.update()
    await settle(source)
    assert sum(r.energy_wh for r in read(source).expected) == 200
    release.set()
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert sum(r.energy_wh for r in read(source).expected) == 200
    await source.async_stop()
