"""Live-derived actuator ramp regression for 2026-11-04 14:31 (#51).

With +2507 W import the first M200 command was 26%, followed by a
measured -646 W export pulse. These tests exercise actual service requests,
not only the pure controller's intentions. No hardware is used by CI.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import runtime as module  # noqa: E402
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402


def entity(value: float, unit: str, **attrs: object) -> SimpleNamespace:
    stamp = datetime.now(UTC)
    return SimpleNamespace(
        state=str(value),
        attributes={
            "unit_of_measurement": unit,
            "step": 0.1 if unit == "%" else 1,
            "min": 0,
            "max": 100 if unit == "%" else 800,
            **attrs,
        },
        last_reported=stamp,
        last_updated=stamp,
    )


def runtime_pair() -> tuple[EMSRuntime, SimpleNamespace]:
    states = {
        "sensor.grid": entity(2507.678, "W"),
        "number.m200": entity(0, "%"),
        "number.m80": entity(0, "W"),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    entry = SimpleNamespace(
        entry_id="ramp-live-replay",
        options={"enabled": True, "feed_in": False, "grid_target_w": 20},
        data={
            "grid": "sensor.grid",
            "invert_grid": False,
            "batteries": [
                {
                    "device_id": "battery1",
                    "soc": 90,
                    "lower": 10,
                    "upper": 100,
                    "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
                },
                {
                    "device_id": "battery2",
                    "soc": 50,
                    "lower": 10,
                    "upper": 100,
                    "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
                },
            ],
        },
    )
    return EMSRuntime(hass, entry), hass


@pytest.fixture(autouse=True)
def isolate_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        module.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None)
    )


@pytest.mark.asyncio
async def test_first_m200_command_cannot_jump_to_26_percent_on_2507_w_import() -> None:
    """The first physical command must not inherit the full 520 W correction."""
    runtime, hass = runtime_pair()
    await runtime._apply({"number.m200": 520}, now=1000)

    command = hass.services.async_call.await_args.args[2]
    assert command["entity_id"] == "number.m200"
    assert 0 < command["value"] <= 1.0, "20 W is 1% of the verified 2 kW M200"
    assert runtime._last_sent["number.m200"] == command["value"]


@pytest.mark.asyncio
async def test_next_m200_increase_after_start_hold_is_still_bounded() -> None:
    """The second request cannot skip from 1% straight to 27%."""
    runtime, hass = runtime_pair()
    runtime._last_sent["number.m200"] = 1.0
    runtime._sent_at["number.m200"] = 900.0
    hass.states.get("number.m200").state = "1"

    await runtime._apply({"number.m200": 540}, now=1000)

    command = hass.services.async_call.await_args.args[2]
    assert command["entity_id"] == "number.m200"
    assert 1.0 < command["value"] <= 2.0


@pytest.mark.asyncio
async def test_watt_based_actuator_has_same_positive_ramp_guard() -> None:
    runtime, hass = runtime_pair()
    await runtime._apply({"number.m80": 520}, now=1000)

    command = hass.services.async_call.await_args.args[2]
    assert command["entity_id"] == "number.m80"
    assert 0 < command["value"] <= 20


@pytest.mark.asyncio
async def test_immediate_emergency_reduction_bypasses_positive_ramp() -> None:
    runtime, hass = runtime_pair()
    runtime._last_sent["number.m200"] = 26.0
    runtime._sent_at["number.m200"] = 1000.0
    hass.states.get("number.m200").state = "26"

    await runtime._apply({"number.m200": 0}, now=1001, emergency=True)

    command = hass.services.async_call.await_args.args[2]
    assert command == {"entity_id": "number.m200", "value": 0}


@pytest.mark.asyncio
async def test_zero_to_zero_does_not_send_fake_pending_command() -> None:
    runtime, hass = runtime_pair()
    await runtime._apply({"number.m200": 0}, now=1000)

    hass.services.async_call.assert_not_awaited()
    assert not runtime._pending
