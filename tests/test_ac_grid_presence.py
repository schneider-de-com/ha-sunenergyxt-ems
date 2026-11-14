"""Replay the Nov 14 dual Deye AC-breaker loss without touching hardware.

The Solarman logger stayed connected while both grid-voltage registers
fell from 226/224 V to zero. A writable number is not evidence that the
inverter is AC-connected. This is an MVP shutdown/partial-failure contract,
not a simulation claiming physical power or device sample timestamps.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.sunenergyxt_ems import runtime as module
from custom_components.sunenergyxt_ems.runtime import EMSRuntime


def sample(value: object, unit: str, **attributes: object) -> SimpleNamespace:
    stamp = datetime.now(UTC)
    return SimpleNamespace(
        state=str(value),
        attributes={"unit_of_measurement": unit, **attributes},
        last_reported=stamp,
        last_updated=stamp,
    )


def configured_two_inverters(monkeypatch: pytest.MonkeyPatch):
    states = {
        "sensor.grid": sample(320, "W"),
        "sensor.soc1": sample(85, "%"),
        "sensor.soc2": sample(66, "%"),
        "number.low": sample(10, "%"),
        "number.high": sample(100, "%"),
        "number.m200": sample(10, "%", min=0, max=100, step=0.1),
        "number.m80": sample(0, "%", min=0, max=100, step=0.1),
        "sensor.m200_grid_voltage": sample(226, "V"),
        "sensor.m80_grid_voltage": sample(224, "V"),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    entry = SimpleNamespace(
        entry_id="ac-loss-replay",
        data={
            "grid": "sensor.grid",
            "invert_grid": False,
            "batteries": [
                {
                    "device_id": "bk1",
                    "soc": "sensor.soc1",
                    "lower": "number.low",
                    "upper": "number.high",
                    "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
                },
                {
                    "device_id": "bk2",
                    "soc": "sensor.soc2",
                    "lower": "number.low",
                    "upper": "number.high",
                    "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
                },
            ],
        },
        options={"enabled": True, "feed_in": False, "grid_target_w": 20},
    )
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    monkeypatch.setattr(module.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None))
    monkeypatch.setattr(module, "registered_connection", lambda *args: None)
    monkeypatch.setattr(module, "registered_dc_output", lambda *args: None)
    # The registry lookup is patched, not an entity-ID string heuristic.
    monkeypatch.setattr(
        module,
        "registered_ac_voltage",
        lambda _hass, entity: {
            "number.m200": "sensor.m200_grid_voltage",
            "number.m80": "sensor.m80_grid_voltage",
        }.get(entity),
        raising=False,
    )
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    runtime._apply = AsyncMock()
    runtime._save_shutdown_obligations = AsyncMock(return_value=True)
    runtime._verify_shutdown = AsyncMock()
    return runtime, states


@pytest.mark.asyncio
async def test_live_ac_breaker_loss_forces_zero_without_disabling_healthy_battery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """226->0 V on M200 while Solarman online; M80 may still cover load."""
    runtime, states = configured_two_inverters(monkeypatch)
    states["sensor.m200_grid_voltage"].state = "0"
    batteries = runtime._batteries()
    assert batteries[0].inverters[0].online is False
    assert batteries[1].inverters[0].online is True

    await runtime.async_update()
    commands = runtime._apply.await_args_list
    assert any(
        call.args[0].get("number.m200") == 0
        and call.kwargs.get("emergency") is True
        for call in commands
    ), "Lose AC at a still-writable M200: dispatch its minimum immediately"
    assert all(
        call.args[0].get("number.m200", 0) <= 0
        for call in commands
    ), "No positive M200 command while its local AC voltage is zero"
    assert any(
        call.args[0].get("number.m80", 0) > 0
        for call in commands
    ), "The independent healthy BK215 must remain eligible"


@pytest.mark.asyncio
async def test_stale_zero_voltage_does_not_rearm_positive_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale zero is not proof that the grid supply has returned."""
    runtime, states = configured_two_inverters(monkeypatch)
    voltage = states["sensor.m200_grid_voltage"]
    voltage.state = "0"
    voltage.last_reported = datetime.now(UTC) - timedelta(minutes=10)
    assert runtime._batteries()[0].inverters[0].online is False
    await runtime.async_update()
    assert all(
        call.args[0].get("number.m200", 0) <= 0
        for call in runtime._apply.await_args_list
    )


@pytest.mark.asyncio
async def test_restored_voltage_requires_fresh_safe_minimum_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """0->246 V is not permission to resume a previous 10% setpoint instantly."""
    runtime, states = configured_two_inverters(monkeypatch)
    voltage = states["sensor.m200_grid_voltage"]
    voltage.state = "0"
    await runtime.async_update()
    runtime._apply.reset_mock()
    voltage.state = "246"
    voltage.last_reported = datetime.now(UTC)
    assert runtime._batteries()[0].inverters[0].online is True
    await runtime.async_update()
    assert "number.m200" in runtime._shutdown_obligations
    assert any(
        call.args[0].get("number.m200") == 0
        and call.kwargs.get("emergency") is True
        for call in runtime._apply.await_args_list
    ), "Recovery must re-establish the minimum before ordinary control"


def test_unmapped_inverter_is_not_assumed_to_have_solarman_voltage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A generic writable number remains usable without that Deye signal."""
    runtime, states = configured_two_inverters(monkeypatch)
    monkeypatch.setattr(module, "registered_ac_voltage", lambda *args: None, raising=False)
    states["sensor.m200_grid_voltage"].state = "0"
    assert all(inverter.online for battery in runtime._batteries() for inverter in battery.inverters)
