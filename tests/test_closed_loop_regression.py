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


@pytest.mark.asyncio
async def test_startup_uses_reported_actuator_limit_as_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a 400 W reported limit and 180 W import, never reset the baseline to zero."""
    hass, entry = configured_slow_inverter()
    hass.states.get("number.inverter").state = "50"  # 400 W of the 800 W rating
    hass.states.get("sensor.grid").state = "180"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    monkeypatch.setattr(runtime_module, "monotonic", lambda: 4000.0)

    await runtime.async_update()

    assert hass.services.async_call.await_count == 1
    assert hass.services.async_call.await_args.args[2]["value"] == 70, (
        "400 W baseline + 160 W correction = 560 W, or 70% of 800 W"
    )


@pytest.mark.asyncio
async def test_small_positive_grid_error_keeps_existing_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not chase 5 W above a 20 W grid target when 0..30 W is acceptable."""
    hass, entry = configured_slow_inverter()
    hass.states.get("number.inverter").state = "50"
    hass.states.get("sensor.grid").state = "25"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    monkeypatch.setattr(runtime_module, "monotonic", lambda: 5000.0)

    await runtime.async_update()

    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_large_load_step_is_bounded_to_old_controller_step_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 1,200 W import must not instantly produce a 1,180 W setpoint jump."""
    hass, entry = configured_slow_inverter()
    hass.states.get("sensor.grid").state = "1200"
    entry.data["batteries"][0]["inverters"][0]["rated_w"] = 2000
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    monkeypatch.setattr(runtime_module, "monotonic", lambda: 6000.0)

    await runtime.async_update()

    assert hass.services.async_call.await_count == 1
    # The previous automation caps a large correction to 520 W.
    assert 0 < hass.services.async_call.await_args.args[2]["value"] <= 26


@pytest.mark.asyncio
async def test_main_swap_never_increases_support_before_previous_main_releases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Main-first transfer is break-before-make, even near the SoC reserve."""
    hass, entry = configured_slow_inverter()
    entry.data["batteries"][0]["soc"] = "sensor.soc_main"
    entry.data["batteries"][0]["inverters"][0]["entity_id"] = "number.main"
    entry.data["batteries"].append(
        {
            "device_id": "bk215-support",
            "soc": "sensor.soc_support",
            "lower": "number.lower",
            "upper": "number.upper",
            "inverters": [{"entity_id": "number.support", "rated_w": 800}],
        }
    )
    original_states = {
        "sensor.grid": hass.states.get("sensor.grid"),
        "number.lower": hass.states.get("number.lower"),
        "number.upper": hass.states.get("number.upper"),
        "sensor.soc_main": sample(80, "%"),
        "sensor.soc_support": sample(70, "%"),
        "number.main": sample(0, "%", min=0, max=100, step=1),
        "number.support": sample(0, "%", min=0, max=100, step=1),
    }
    hass.states.get = original_states.get
    clock = [7000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    # Force an urgent role switch before the previous main acknowledges its limit.
    original_states["sensor.soc_main"].state = "10"
    clock[0] = 7001.0
    await runtime.async_update()
    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert {"entity_id": "number.main", "value": 0} in commands
    assert not any(
        command["entity_id"] == "number.support" and command["value"] > 0
        for command in commands
    ), "Do not increase the new main before previous inverter is confirmed at zero"
