"""Regression scenarios based on live EMS behavior: delayed device feedback.

These tests describe external behavior, not internal controller implementation.
They must fail against 0.1.0b2 until acknowledged writes and post-write meter
samples gate further normal corrections. Safety reductions are exempt.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import runtime as runtime_module  # noqa: E402
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402


def sample(value: object, unit: str, **attributes: object) -> SimpleNamespace:
    stamp = datetime.now(UTC)
    return SimpleNamespace(
        state=str(value),
        attributes={"unit_of_measurement": unit, **attributes},
        last_reported=stamp,
        last_updated=stamp,
    )


def configured_slow_inverter() -> tuple[SimpleNamespace, SimpleNamespace]:
    """The HA service responds, but the Solarman number entity stays unchanged."""
    states = {
        "sensor.grid": sample(300, "W"),
        "sensor.soc": sample(80, "%"),
        "number.lower": sample(10, "%"),
        "number.upper": sample(90, "%"),
        "number.inverter": sample(0, "%", min=0, max=100, step=1),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    entry = SimpleNamespace(
        entry_id="test-entry",
        options={"enabled": True, "feed_in": False, "grid_target_w": 20},
        data={
            "grid": "sensor.grid",
            "invert_grid": False,
            "batteries": [
                {
                    "device_id": "bk215-1",
                    "soc": "sensor.soc",
                    "lower": "number.lower",
                    "upper": "number.upper",
                    "inverters": [{"entity_id": "number.inverter", "rated_w": 800}],
                },
            ],
        },
    )
    return hass, entry


@pytest.fixture(autouse=True)
def no_real_home_assistant_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_module, "async_dispatcher_send", lambda *args: None)
    monkeypatch.setattr(
        runtime_module.er,
        "async_get",
        lambda hass: SimpleNamespace(async_get=lambda _: None),
    )


@pytest.mark.asyncio
async def test_delayed_solarman_ack_does_not_accumulate_unsent_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Repeated meter samples must not ratchet an unacknowledged 35% command to 100%."""
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])

    await runtime.async_update()
    first = hass.services.async_call.await_args.args[2]["value"]
    assert first == 35
    assert hass.services.async_call.await_count == 1

    for tick in range(1, 6):
        clock[0] = 1000.0 + tick * 5.0
        # Meter has a new sample, but the Deye number still shows 0%.
        hass.states.get("sensor.grid").last_reported = (
            datetime.now(UTC) + timedelta(milliseconds=tick)
        )
        await runtime.async_update()
        assert hass.services.async_call.await_count == 1, (
            "Do not dispatch a new normal command before the previous one is acknowledged"
        )
        assert runtime.controller.state.outputs["number.inverter"] <= 280, (
            "Do not accumulate planner output that has not reached the inverter"
        )


@pytest.mark.asyncio
async def test_ack_without_new_post_write_meter_sample_must_not_recalculate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An acknowledged inverter limit alone is not a fresh grid response."""
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [2000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])

    await runtime.async_update()
    assert hass.services.async_call.await_count == 1
    # Simulate inverter entity acknowledging the commanded 35%.
    hass.states.get("number.inverter").state = "35"
    clock[0] = 2025.0
    # Do not change the meter timestamp: the meter sample predates the write.
    await runtime.async_update()
    assert hass.services.async_call.await_count == 1
    assert runtime.controller.state.outputs["number.inverter"] <= 280


@pytest.mark.asyncio
async def test_reserve_shutdown_bypasses_pending_feedback_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Safety must still reduce the limit immediately when SoC reaches 10%."""
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [3000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])

    await runtime.async_update()
    assert hass.services.async_call.await_count == 1
    hass.states.get("sensor.soc").state = "10"
    clock[0] = 3001.0
    await runtime.async_update()
    assert hass.services.async_call.await_count == 2
    assert hass.services.async_call.await_args.args[2]["value"] == 0
