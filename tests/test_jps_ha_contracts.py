"""Live-shaped HA runtime contracts for optional EV charging and PID startup."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import runtime as module  # noqa: E402
from custom_components.sunenergyxt_ems.inputs import (  # noqa: E402
    ev3600_charging,
    fresh_power,
)
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402


def sample(value: object, unit: str = "W", *, seconds_old: int = 0):
    stamp = datetime.now(UTC) - timedelta(seconds=seconds_old)
    return SimpleNamespace(
        state=str(value),
        attributes={"unit_of_measurement": unit, "min": 0, "max": 800, "step": 1},
        last_reported=stamp,
        last_updated=stamp,
    )


def configured():
    states = {
        "sensor.grid": sample(420),
        "sensor.soc": sample(90, "%"),
        "number.inverter": sample(0),
        "switch.ev": sample("off"),
        "sensor.ev": sample(300),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
        config_entries=SimpleNamespace(async_update_entry=lambda entry, *, options: setattr(entry, "options", options)),
    )
    entry = SimpleNamespace(
        entry_id="jps-test",
        data={"grid": "sensor.grid", "batteries": [
            {"device_id": "bk1", "soc": "sensor.soc", "lower": 10, "upper": 90,
             "inverters": [{"entity_id": "number.inverter", "rated_w": 800}]}
        ]},
        options={"enabled": True, "feed_in": False, "ev3600": {"bk1": {"mode": "switch.ev", "power": "sensor.ev"}}},
    )
    return hass, entry, states


@pytest.fixture(autouse=True)
def avoid_external_writes(monkeypatch):
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    monkeypatch.setattr(module.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None))
    monkeypatch.setattr(module, "registered_dc_output", lambda *args: None)


@pytest.mark.asyncio
async def test_ev3600_charging_blocks_commands_on_only_its_tower():
    hass, entry, states = configured()
    states["switch.ev"].state = "on"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert "bk1" in runtime.controller.state.ev3600_active
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_ev3600_starts_mid_command_emergency_zero_bypasses_ack_wait():
    hass, entry, states = configured()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] > 0
    states["switch.ev"].state = "on"
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 0


def test_stale_power_or_unknown_ev_state_never_looks_like_not_charging():
    hass, entry, states = configured()
    opts = entry.options["ev3600"]["bk1"]
    states["switch.ev"].state = "on"
    assert ev3600_charging(hass, opts) is True
    states["sensor.ev"].last_reported -= timedelta(minutes=5)
    assert ev3600_charging(hass, opts) is None
    states["switch.ev"].state = "unavailable"
    assert ev3600_charging(hass, opts) is None
    assert ev3600_charging(hass, None) is False
    assert fresh_power(hass, "sensor.ev") is None

@pytest.mark.asyncio
async def test_startup_hold_prevents_normal_writes_but_releases_after_30s(monkeypatch):
    hass, entry, states = configured()
    entry.options["ev3600"] = {}
    timeline = [100.0]
    monkeypatch.setattr(module, "monotonic", lambda: timeline[0])
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert runtime._start_hold.active("number.inverter", 105)
    assert hass.services.async_call.await_count == 1

    states["number.inverter"].state = "200"
    states["number.inverter"].last_reported += timedelta(seconds=2)
    states["sensor.grid"].state = "500"
    states["sensor.grid"].last_reported += timedelta(seconds=2)
    timeline[0] += 8
    await runtime.async_update()
    assert hass.services.async_call.await_count == 1

    states["sensor.grid"].last_reported += timedelta(seconds=10)
    timeline[0] += 31
    await runtime.async_update()
    assert hass.services.async_call.await_count >= 2

@pytest.mark.asyncio
async def test_startup_hold_allows_reducing_percent_inverter_on_real_export():
    """Compare % with %, not target watts with native reported percentage."""
    hass, entry, states = configured()
    entry.options["ev3600"] = {}
    states["number.inverter"].state = "10"
    states["number.inverter"].attributes["unit_of_measurement"] = "%"
    states["number.inverter"].attributes["max"] = 100
    states["number.inverter"].attributes["step"] = 0.1
    runtime = EMSRuntime(hass, entry)
    runtime._last_sent["number.inverter"] = 10.0
    runtime._start_hold.record("number.inverter", before_w=0, after_w=80, now=90)
    runtime._grid_filtered_w = -700.0
    await runtime._apply({"number.inverter": 40}, now=100)
    assert hass.services.async_call.await_args.args[2]["value"] == 5.0

@pytest.mark.asyncio
async def test_ev3600_safety_stop_is_not_blocked_by_missing_grid_at_startup():
    """A protected BK215 must not keep a previous nonzero limit while grid is offline."""
    hass, entry, states = configured()
    states["switch.ev"].state = "on"
    states["number.inverter"].state = "300"
    states["sensor.grid"].state = "unavailable"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2] == {
        "entity_id": "number.inverter",
        "value": 0,
    }
