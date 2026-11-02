"""Runtime tests with mocked Home Assistant entities and services."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import runtime as module  # noqa: E402
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402


def sample(value: object, unit: str = "%", *, seconds_old: int = 0, **attrs: object):
    stamp = datetime.now(UTC) - timedelta(seconds=seconds_old)
    return SimpleNamespace(
        state=str(value),
        attributes={"unit_of_measurement": unit, **attrs},
        last_reported=stamp,
        last_updated=stamp,
    )


def configured(*, grid=420, soc=70, lower=10, inverter_unit="W", old=0):
    data = {
        "grid": "sensor.grid",
        "invert_grid": False,
        "batteries": [{
            "device_id": "battery-1", "soc": "sensor.soc",
            "lower": "number.lower", "upper": "number.upper",
            "inverters": [{"entity_id": "number.inverter", "rated_w": 800}],
        }],
    }
    states = {
        "sensor.grid": sample(grid, "W", seconds_old=old),
        "sensor.soc": sample(soc),
        "number.lower": sample(lower),
        "number.upper": sample(90),
        "number.inverter": sample(0, inverter_unit, min=0, max=100 if inverter_unit == "%" else 800, step=1),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    entry = SimpleNamespace(
        data=data,
        options={"enabled": True, "feed_in": False},
        entry_id="test-entry",
    )
    return hass, entry


@pytest.fixture(autouse=True)
def fake_dispatcher(monkeypatch):
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    monkeypatch.setattr(
        module.er, "async_get", lambda hass: SimpleNamespace(async_get=lambda entity: None)
    )


@pytest.mark.asyncio
async def test_positive_watts_are_sent_through_number_integration():
    hass, entry = configured()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    hass.services.async_call.assert_awaited_once()
    assert hass.services.async_call.await_args.args[:2] == ("number", "set_value")
    assert hass.services.async_call.await_args.args[2]["value"] == 400


@pytest.mark.asyncio
async def test_positive_percent_limit_is_converted_from_watts():
    hass, entry = configured(inverter_unit="%")
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 50


@pytest.mark.asyncio
async def test_negative_stale_grid_does_not_start_live_control():
    hass, entry = configured(old=90)
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()
    assert runtime.status == "waiting"


@pytest.mark.asyncio
async def test_negative_missing_soc_only_waits_without_new_writes():
    hass, entry = configured(soc="unavailable")
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_manual_disable_sends_one_minimum_limit():
    hass, entry = configured()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    hass.services.async_call.reset_mock()
    await runtime.async_disable()
    assert hass.services.async_call.await_args.args[2]["value"] == 0
    await runtime.async_update()
    assert hass.services.async_call.await_count == 1


@pytest.mark.asyncio
async def test_implausible_grid_value_does_not_start_controller():
    hass, entry = configured(grid=float("nan"))
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_failed_write_is_reported():
    from homeassistant.exceptions import HomeAssistantError

    hass, entry = configured()
    hass.services.async_call.side_effect = HomeAssistantError("write failed")
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert runtime.status == "write_error"
    assert "number.inverter" in (runtime.last_error or "")



@pytest.mark.asyncio
async def test_implausible_rounding_cannot_exceed_inverter_maximum():
    hass, entry = configured(grid=804, inverter_unit="%")
    state = hass.states.get("number.inverter")
    state.attributes["max"] = 98
    state.attributes["step"] = 5
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    value = hass.services.async_call.await_args.args[2]["value"]
    assert value <= 98


@pytest.mark.asyncio
async def test_implausible_future_meter_timestamp_is_rejected():
    hass, entry = configured(grid=500)
    state = hass.states.get("sensor.grid")
    state.last_reported = datetime.now(UTC) + timedelta(minutes=10)
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()



@pytest.mark.asyncio
async def test_negative_offline_inverter_is_reported_during_manual_stop():
    hass, entry = configured()
    hass.states.get("number.inverter").state = "unavailable"
    runtime = EMSRuntime(hass, entry)
    await runtime.async_disable()
    hass.services.async_call.assert_not_awaited()
    assert runtime.status == "write_error"
    assert "number.inverter" in (runtime.last_error or "")



@pytest.mark.asyncio
async def test_negative_rate_limited_inverter_skips_normal_fast_changes(monkeypatch):
    hass, entry = configured()
    runtime = EMSRuntime(hass, entry)
    monkeypatch.setattr(runtime, "_minimum_interval", lambda entity: 300.0)
    runtime._last_sent["number.inverter"] = 500
    runtime._sent_at["number.inverter"] = 1
    await runtime._apply({"number.inverter": 100}, now=11)
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_positive_emergency_reduction_bypasses_rate_limit(monkeypatch):
    hass, entry = configured()
    runtime = EMSRuntime(hass, entry)
    monkeypatch.setattr(runtime, "_minimum_interval", lambda entity: 300.0)
    runtime._last_sent["number.inverter"] = 500
    runtime._sent_at["number.inverter"] = 1
    await runtime._apply({"number.inverter": 0}, now=11, emergency=True)
    assert hass.services.async_call.await_args.args[2]["value"] == 0
