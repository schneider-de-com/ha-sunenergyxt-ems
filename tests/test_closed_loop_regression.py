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
    """Repeated meter samples must not ratchet an unacknowledged command upward."""
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])

    await runtime.async_update()
    first = hass.services.async_call.await_args.args[2]["value"]
    assert 0 < first <= 65  # bounded by the 520 W maximum step at 800 W rating
    assert hass.services.async_call.await_count == 1

    for tick in range(1, 6):
        clock[0] = 1000.0 + tick * 5.0
        # Meter has a new sample, but the Deye number still shows 0%.
        hass.states.get("sensor.grid").last_reported = datetime.now(UTC) + timedelta(
            milliseconds=tick
        )
        await runtime.async_update()
        assert hass.services.async_call.await_count == 1, (
            "Do not dispatch a new normal command before the previous one is acknowledged"
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
    # Simulate acknowledgement of the observed command, not the legacy algorithm.
    commanded = hass.services.async_call.await_args.args[2]["value"]
    hass.states.get("number.inverter").state = str(commanded)
    hass.states.get("number.inverter").last_reported = datetime.now(UTC)
    clock[0] = 2025.0
    # Do not change the meter timestamp: the meter sample predates the write.
    await runtime.async_update()
    assert hass.services.async_call.await_count == 1


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
    assert runtime.controller.state.outputs["number.inverter"] == 480
    assert hass.services.async_call.await_args.args[2]["value"] == 52, (
        "The requested 480 W is ramped from the reported 400 W to 416 W (52%)"
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
        command["entity_id"] == "number.support" and command["value"] > 0 for command in commands
    ), "Do not increase the new main before previous inverter is confirmed at zero"


@pytest.mark.asyncio
async def test_ack_and_new_grid_sample_allow_next_bounded_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify recovery after real (simulated) entity ACK plus a fresh meter sample."""
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [8000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])

    await runtime.async_update()
    first = hass.services.async_call.await_args.args[2]["value"]
    assert hass.services.async_call.await_count == 1
    assert runtime.status == "waiting_feedback"

    # The HA number integration reports the requested setpoint.
    hass.states.get("number.inverter").state = str(first)
    hass.states.get("number.inverter").last_reported = datetime.now(UTC) + timedelta(milliseconds=1)
    # The Shelly meter also reports a newer observation, after the write.
    hass.states.get("sensor.grid").last_reported = datetime.now(UTC) + timedelta(milliseconds=2)
    clock[0] += 21.0  # Feedback is ready but the new 30 s startup hold is not.
    await runtime.async_update()
    assert hass.services.async_call.await_count == 1
    assert runtime._start_hold.active("number.inverter", clock[0])

    # A newer meter reading after the hold and the acknowledged actuator update
    # must allow the next bounded correction; old observations may not be reused.
    clock[0] += 11.0
    hass.states.get("sensor.grid").last_reported = datetime.now(UTC) + timedelta(milliseconds=3)
    await runtime.async_update()

    assert hass.services.async_call.await_count == 2
    second = hass.services.async_call.await_args.args[2]["value"]
    assert first < second <= 100
    assert runtime.status == "waiting_feedback"


@pytest.mark.asyncio
async def test_new_load_does_not_override_unacknowledged_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new load is not permission to overwrite an unconfirmed inverter limit."""
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [9000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])

    await runtime.async_update()
    assert hass.services.async_call.await_count == 1
    first_limit = hass.services.async_call.await_args.args[2]["value"]
    assert first_limit > 0

    for tick in range(1, 6):
        clock[0] = 9000.0 + 5 * tick
        meter = hass.states.get("sensor.grid")
        meter.state = "800"  # household demand changed significantly
        meter.last_reported = datetime.now(UTC) + timedelta(milliseconds=tick)
        # The inverter still reports its old 0% limit (no acknowledgement).
        await runtime.async_update()
        assert hass.services.async_call.await_count == 1, (
            "Never replace an in-flight command just because the grid load changed"
        )
        assert runtime.status in ("waiting_feedback", "write_error")


@pytest.mark.asyncio
async def test_timeout_requests_safe_minimum_and_rejects_late_positive_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unconfirmed increase must not remain the last command indefinitely."""
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [10000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])

    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] > 0
    for seconds in (5, 10, 15, 20, 25, 30):
        clock[0] = 10000.0 + seconds
        hass.states.get("sensor.grid").last_reported = datetime.now(UTC)
        await runtime.async_update()

    assert hass.services.async_call.await_count == 2
    assert hass.services.async_call.await_args.args[2]["value"] == 0
    assert runtime.status == "write_error"
    assert "acknowledged" in runtime.last_error

    # Late positive feedback is not confirmation of the subsequent zero request.
    inverter = hass.states.get("number.inverter")
    inverter.state = "2"
    inverter.last_reported = datetime.now(UTC) + timedelta(milliseconds=1)
    clock[0] += 5
    hass.states.get("sensor.grid").last_reported = datetime.now(UTC)
    await runtime.async_update()
    assert hass.services.async_call.await_count == 2
    assert runtime.status == "write_error"

    # Fresh readback of the safe command permits automatic recovery; an old
    # positive readback above did not.  No manual reset is required.
    inverter.state = "0"
    inverter.last_reported = datetime.now(UTC) + timedelta(milliseconds=2)
    hass.states.get("sensor.grid").last_reported = datetime.now(UTC)
    clock[0] += 20
    await runtime.async_update()
    assert hass.services.async_call.await_count == 3
    assert hass.services.async_call.await_args.args[2]["value"] > 0


@pytest.mark.asyncio
async def test_unconfirmed_increase_is_recovered_after_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    monkeypatch.setattr(runtime_module, "monotonic", lambda: 20000.0)

    async def check_persisted_before_write(domain, action, data, **kwargs):
        if data["value"] > 0:
            assert (await runtime._shutdown_store.async_load())["inflight"] == ["number.inverter"]

    hass.services.async_call.side_effect = check_persisted_before_write
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] > 0
    assert (await runtime._shutdown_store.async_load())["inflight"] == ["number.inverter"]

    restarted = EMSRuntime(hass, entry)
    restarted._shutdown_store = runtime._shutdown_store
    await restarted._load_shutdown_obligations()
    restarted._running = True
    await restarted.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 0
    assert "number.inverter" in restarted._shutdown_obligations


@pytest.mark.asyncio
async def test_increase_is_not_sent_when_journal_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    runtime._shutdown_store.async_save = AsyncMock(side_effect=OSError("disk full"))
    monkeypatch.setattr(runtime_module, "monotonic", lambda: 30000.0)
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()
    assert runtime.status == "write_error"
    assert "persist" in runtime.last_error.lower()


@pytest.mark.asyncio
async def test_unconfirmed_inverter_does_not_block_other_battery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass, entry = configured_slow_inverter()
    states = hass.states.get.__self__
    states["sensor.soc2"] = sample(70, "%")
    states["number.inverter2"] = sample(0, "%", min=0, max=100, step=1)
    entry.data["batteries"].append(
        {
            "device_id": "bk215-2",
            "soc": "sensor.soc2",
            "lower": "number.lower",
            "upper": "number.upper",
            "inverters": [{"entity_id": "number.inverter2", "rated_w": 800}],
        }
    )
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [40000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["entity_id"] == "number.inverter"
    clock[0] += 30
    states["sensor.grid"].last_reported = datetime.now(UTC)
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 0
    clock[0] += 5
    states["sensor.grid"].last_reported = datetime.now(UTC)
    await runtime.async_update()
    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert any(call["entity_id"] == "number.inverter2" and call["value"] > 0 for call in commands)


@pytest.mark.asyncio
@pytest.mark.parametrize("grid", [-150, -800])
async def test_unconfirmed_decrease_is_journaled_before_write_and_recovered(
    monkeypatch: pytest.MonkeyPatch, grid: int
) -> None:
    hass, entry = configured_slow_inverter()
    hass.states.get("number.inverter").state = "50"
    hass.states.get("sensor.grid").state = str(grid)
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    monkeypatch.setattr(runtime_module, "monotonic", lambda: 25000.0)

    async def verify_journal(domain, action, data, **kwargs):
        assert data["value"] < 50
        assert (await runtime._shutdown_store.async_load())["inflight"] == ["number.inverter"]

    hass.services.async_call.side_effect = verify_journal
    await runtime.async_update()
    assert hass.services.async_call.await_count == 1
    restarted = EMSRuntime(hass, entry)
    restarted._shutdown_store = runtime._shutdown_store
    await restarted._load_shutdown_obligations()
    assert "number.inverter" in restarted._shutdown_obligations


@pytest.mark.asyncio
async def test_two_feedback_timeouts_each_allow_a_safe_shutdown_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass, entry = configured_slow_inverter()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [50000.0]
    monkeypatch.setattr(runtime_module, "monotonic", lambda: clock[0])

    async def tick(seconds):
        clock[0] += seconds
        hass.states.get("sensor.grid").last_reported = datetime.now(UTC)
        await runtime.async_update()

    await runtime.async_update()
    await tick(30)  # timeout and first safe command
    await tick(30)  # first safe command is not acknowledged
    await tick(5)  # retry safe command
    assert hass.services.async_call.await_count == 3
    inverter = hass.states.get("number.inverter")
    inverter.state = "0"
    inverter.last_reported = datetime.now(UTC) + timedelta(milliseconds=2)
    await tick(20)  # safe readback and automatic reactivation
    assert hass.services.async_call.await_args.args[2]["value"] > 0
    await tick(30)  # next positive command times out
    await tick(30)  # first safe command is not acknowledged again
    before_retry = hass.services.async_call.await_count
    await tick(5)
    assert hass.services.async_call.await_count == before_retry + 1
    assert hass.services.async_call.await_args.args[2]["value"] == 0


@pytest.mark.asyncio
async def test_reserve_reduction_attempted_even_if_journal_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hass, entry = configured_slow_inverter()
    hass.states.get("number.inverter").state = "50"
    hass.states.get("sensor.soc").state = "10"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    runtime._shutdown_store.async_save = AsyncMock(side_effect=OSError("disk full"))
    monkeypatch.setattr(runtime_module, "monotonic", lambda: 60000.0)
    await runtime.async_update()
    assert hass.services.async_call.await_count == 1
    assert hass.services.async_call.await_args.args[2]["value"] == 0
    assert runtime.status == "write_error"
    assert "persist" in runtime.last_error.lower()
