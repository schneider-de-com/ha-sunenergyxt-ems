"""Behavior-first regressions for real-world disabled-state and Solarman failures.

The incidents were observed on 2026-11-03 with the live 0.1.0b2 EMS.
These tests intentionally exercise HA-visible commands, not private algorithm flags.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import runtime as module  # noqa: E402
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402


def sample(value: object, unit: str = "%", **attrs: object) -> SimpleNamespace:
    timestamp = datetime.now(UTC)
    return SimpleNamespace(
        state=str(value),
        attributes={"unit_of_measurement": unit, **attrs},
        last_reported=timestamp,
        last_updated=timestamp,
    )


def configured_two_bk215() -> tuple[SimpleNamespace, SimpleNamespace]:
    states = {
        "sensor.grid": sample(250, "W"),
        "sensor.soc1": sample(80),
        "sensor.soc2": sample(11),
        "number.lower1": sample(10),
        "number.lower2": sample(10),
        "number.upper1": sample(90),
        "number.upper2": sample(90),
        "number.m200": sample(0, min=0, max=100, step=0.1),
        "number.m80": sample(100, min=0, max=100, step=0.1),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    entry = SimpleNamespace(
        entry_id="mvp",
        options={"enabled": True, "feed_in": False, "grid_target_w": 20},
        data={
            "grid": "sensor.grid",
            "invert_grid": False,
            "batteries": [
                {
                    "device_id": "bk1",
                    "soc": "sensor.soc1",
                    "lower": "number.lower1",
                    "upper": "number.upper1",
                    "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
                },
                {
                    "device_id": "bk2",
                    "soc": "sensor.soc2",
                    "lower": "number.lower2",
                    "upper": "number.upper2",
                    "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
                },
            ],
        },
    )
    return hass, entry


@pytest.fixture(autouse=True)
def no_physical_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    monkeypatch.setattr(
        module.er, "async_get", lambda hass: SimpleNamespace(async_get=lambda _: None)
    )


@pytest.mark.asyncio
async def test_disable_reissues_zero_when_cached_zero_but_m80_reports_100() -> None:
    """A reconnection can make the real setpoint diverge from the last command."""
    hass, entry = configured_two_bk215()
    runtime = EMSRuntime(hass, entry)
    runtime._last_sent.update({"number.m200": 0, "number.m80": 0})
    runtime._sent_at.update({"number.m200": 100, "number.m80": 100})
    await runtime.async_disable()
    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert any(
        command["entity_id"] == "number.m80" and command["value"] == 0 for command in commands
    ), "A cached zero must not suppress a real emergency shutdown of M80"


@pytest.mark.asyncio
async def test_disable_waits_for_ack_instead_of_announcing_success() -> None:
    """The service returning does not mean that Solarman accepted a command."""
    hass, entry = configured_two_bk215()
    runtime = EMSRuntime(hass, entry)
    await runtime.async_disable()
    assert runtime.status != "disabled", "Shutdown without fresh readback is unconfirmed"
    assert "number.m80" in runtime._shutdown_pending

    inverter = hass.states.get("number.m80")
    inverter.state = "0"
    inverter.last_reported += timedelta(seconds=1)
    entry.options["enabled"] = False
    runtime._running = True
    await runtime.async_update()
    assert "number.m80" not in runtime._shutdown_pending
    assert runtime.status == "disabled"


@pytest.mark.asyncio
async def test_unconfirmed_shutdown_timeout_remains_visible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass, entry = configured_two_bk215()
    runtime = EMSRuntime(hass, entry)
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    await runtime.async_disable()
    clock[0] += 35
    entry.options["enabled"] = False
    runtime._running = True
    await runtime.async_update()
    assert runtime.status == "write_error"
    assert "number.m80" in (runtime.last_error or "")


@pytest.mark.asyncio
async def test_non_task_solarman_cancellation_is_reported_and_does_not_ack() -> None:
    """Solarman's cancelled request is not a successful inverter write."""
    hass, entry = configured_two_bk215()
    hass.services.async_call.side_effect = asyncio.CancelledError()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert runtime.status == "write_error"
    assert "number." in (runtime.last_error or "")
    assert not runtime._pending
    assert not runtime._last_sent


@pytest.mark.asyncio
async def test_genuine_parent_task_cancellation_is_not_swallowed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass, entry = configured_two_bk215()
    hass.services.async_call.side_effect = asyncio.CancelledError()
    monkeypatch.setattr(
        module.asyncio, "current_task", lambda: SimpleNamespace(cancelling=lambda: 1)
    )
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    with pytest.raises(asyncio.CancelledError):
        await runtime.async_update()
