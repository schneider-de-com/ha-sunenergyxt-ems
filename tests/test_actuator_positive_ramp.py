"""Live-derived actuator ramp regression for 2026-11-04 14:31 (#51).

With +2507 W import the first M200 command was 26%, followed by a
measured -646 W export pulse. These tests exercise actual service requests,
not only the pure controller's intentions. No hardware is used by CI.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
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
    monkeypatch.setattr(module.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None))


@pytest.mark.asyncio
async def test_first_m200_command_cannot_jump_to_26_percent_on_2507_w_import() -> None:
    """The first physical command must not inherit the full 520 W correction."""
    runtime, hass = runtime_pair()
    await runtime._apply({"number.m200": 520}, now=1000)

    command = hass.services.async_call.await_args.args[2]
    assert command["entity_id"] == "number.m200"
    assert 0 < command["value"] <= 0.1, (
        "20 W nominal still commands unsafe 1%; 0.1% is the tested starting step"
    )
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
async def test_percent_m80_first_step_is_not_2_5_percent() -> None:
    """A 20 W nominal step is already 2.5% of the M80 and physically unsafe."""
    runtime, hass = runtime_pair()
    hass.states.get("number.m80").attributes["unit_of_measurement"] = "%"
    hass.states.get("number.m80").attributes["step"] = 0.1
    hass.states.get("number.m80").attributes["max"] = 100

    await runtime._apply({"number.m80": 520}, now=1000)

    command = hass.services.async_call.await_args.args[2]
    assert command["entity_id"] == "number.m80"
    assert 0 < command["value"] <= 0.1


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


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reported,confirmed,expected",
    [
        ("2.29", True, 3.3),
        ("2.29", False, None),
        ("2.24", True, None),
        ("2.39", True, 3.3),
    ],
)
async def test_fine_ramp_after_off_grid_readback(
    reported: str, confirmed: bool, expected: float | None
) -> None:
    """A confirmed 2.3% command must progress within 20 W after 2.29% feedback."""
    runtime, hass = runtime_pair()
    entity_id = "number.m200"
    runtime._last_sent[entity_id] = 2.3
    runtime._sent_at[entity_id] = 900.0
    hass.states.get(entity_id).state = reported
    runtime._hardware_feedback_matches = AsyncMock(return_value=confirmed)

    await runtime._apply({entity_id: 520}, now=1000)

    if expected is None:
        hass.services.async_call.assert_not_awaited()
    else:
        assert hass.services.async_call.await_args_list
        assert hass.services.async_call.await_args.args[2] == {
            "entity_id": entity_id,
            "value": expected,
        }


@pytest.mark.asyncio
async def test_acknowledged_m200_reaches_household_load_within_five_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An ideal 2 kW actuator must not need 27 minutes for a 240 W load.

    Replay the real starting load through the normal runtime entry point.
    The plant follows nominal limits immediately, so this isolates software
    settling time. Hardware overshoot still needs independent live evidence.
    """
    trace = json.loads(
        (Path(__file__).parent / "fixtures" / "m200_slow_ramp_20261105.json").read_text()
    )
    assert trace["series"]["limit_pct"][-1][1] == 1.4
    load_w = trace["series"]["grid_w"][0][1]
    runtime, hass = runtime_pair()
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    commands: list[tuple[float, float]] = []
    grid = hass.states.get("sensor.grid")
    inverter = hass.states.get("number.m200")
    origin = datetime.now(UTC)
    monkeypatch.setattr(module, "utcnow", lambda: origin + timedelta(seconds=clock[0] - 1000))
    stamp = module.utcnow()

    async def acknowledge(domain, service, data, **kwargs):
        assert domain == "number" and service == "set_value"
        if data["entity_id"] == "number.m200":
            inverter.state = str(data["value"])
            inverter.last_reported = stamp + timedelta(microseconds=1)
            commands.append((clock[0], data["value"]))

    hass.services.async_call.side_effect = acknowledge
    for tick in range(61):
        clock[0] = 1000.0 + tick * 5
        stamp = module.utcnow()
        grid.state = str(load_w - float(inverter.state) * 20)
        grid.last_reported = stamp
        await runtime.async_update()

    assert commands[0][1] <= 0.1
    assert all(b[1] - a[1] <= 1.00001 for a, b in pairwise(commands))
    assert all(b[0] - a[0] >= 20 for a, b in pairwise(commands))
    remaining_grid_w = load_w - float(inverter.state) * 20
    assert 0 <= remaining_grid_w <= 50, (
        f"After five minutes, grid import is still {remaining_grid_w:.1f} W, "
        f"limit {inverter.state}% after {len(commands)} commands"
    )


@pytest.mark.asyncio
async def test_sustained_import_accelerates_bounded_acknowledged_increases() -> None:
    """Three real high-import samples permit a 20 W step without a 20 s delay."""
    runtime, hass = runtime_pair()
    runtime._last_sent["number.m200"] = 1.0
    runtime._sent_at["number.m200"] = 1000.0
    hass.states.get("number.m200").state = "1"
    for i, watts in enumerate((1200.0, 1150.0, 1100.0)):
        runtime._grid_filter.observe(watts, i)
    runtime._grid_raw_w = 1100.0

    await runtime._apply({"number.m200": 540}, now=1010.0)

    hass.services.async_call.assert_awaited_once()
    command = hass.services.async_call.await_args.args[2]
    assert command["entity_id"] == "number.m200"
    assert 1.0 < command["value"] <= 2.0


@pytest.mark.parametrize("gate", ["feed_in", "startup_hold"])
@pytest.mark.asyncio
async def test_sustained_import_cannot_bypass_feed_in_or_startup_hold(gate: str) -> None:
    """The current user's enabled feed-in setting keeps acceleration inactive."""
    runtime, hass = runtime_pair()
    runtime._last_sent["number.m200"] = 1.0
    runtime._sent_at["number.m200"] = 1000.0
    hass.states.get("number.m200").state = "1"
    for i, watts in enumerate((1200.0, 1150.0, 1100.0)):
        runtime._grid_filter.observe(watts, i)
    runtime._grid_raw_w = 1100.0
    if gate == "feed_in":
        runtime.controller.feed_in = True
    else:
        runtime._start_hold.record("number.m200", before_w=0, after_w=20, now=1000.0)
    await runtime._apply({"number.m200": 540}, now=1010.0)
    hass.services.async_call.assert_not_awaited()
    await runtime._apply({"number.m200": 540}, now=1031.0)
    hass.services.async_call.assert_awaited_once()
    command = hass.services.async_call.await_args.args[2]
    assert 1.0 < command["value"] <= 2.0


@pytest.mark.asyncio
async def test_single_high_import_sample_cannot_accelerate_increase() -> None:
    """A single load spike retains the existing twenty-second interval."""
    runtime, hass = runtime_pair()
    runtime._last_sent["number.m200"] = 1.0
    runtime._sent_at["number.m200"] = 1000.0
    hass.states.get("number.m200").state = "1"
    for i, watts in enumerate((20.0, 25.0, 1100.0)):
        runtime._grid_filter.observe(watts, i)
    runtime._grid_raw_w = 1100.0

    await runtime._apply({"number.m200": 540}, now=1010.0)

    hass.services.async_call.assert_not_awaited()


def test_sustained_import_requires_three_distinct_samples() -> None:
    """Duplicate observations, lower load and reset cannot arm the faster path."""
    meter_filter = module.GridMedianFilter()
    meter_filter.observe(1100.0, 1)
    meter_filter.observe(1100.0, 1)
    meter_filter.observe(1100.0, 1)
    assert not meter_filter.sustained_import()
    meter_filter.observe(1100.0, 2)
    meter_filter.observe(1100.0, 3)
    assert meter_filter.sustained_import()
    meter_filter.observe(100.0, 4)
    assert not meter_filter.sustained_import()
    meter_filter.reset()
    assert not meter_filter.sustained_import()


@pytest.mark.asyncio
async def test_high_import_does_not_bypass_delayed_hardware_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sustained load must not override a simulated mismatching device readback."""
    runtime, hass = runtime_pair()
    runtime._running = True
    clock = [1000.0]
    origin = datetime.now(UTC)
    monkeypatch.setattr(module, "utcnow", lambda: origin + timedelta(seconds=clock[0] - 1000))
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    runtime._hardware_feedback_matches = AsyncMock(return_value=False)
    grid = hass.states.get("sensor.grid")
    inverter = hass.states.get("number.m200")

    await runtime.async_update()
    first = hass.services.async_call.await_args.args[2]["value"]
    assert first > 0
    inverter.state = str(first)
    inverter.last_reported = origin + timedelta(milliseconds=1)

    for seconds in (5, 10, 15, 20, 25):
        clock[0] = 1000.0 + seconds
        grid.state = "2300"
        grid.last_reported = module.utcnow()
        await runtime.async_update()
        assert hass.services.async_call.await_count == 1
        assert runtime.status == "waiting_feedback"

    assert runtime._grid_filter.sustained_import()
    runtime._hardware_feedback_matches.return_value = True
    clock[0] = 1031.0
    grid.last_reported = module.utcnow()
    await runtime.async_update()

    assert hass.services.async_call.await_count == 2
    next_limit = hass.services.async_call.await_args.args[2]["value"]
    assert first < next_limit <= first + 1.0  # At most 20 W on a 2 kW inverter.


@pytest.mark.asyncio
async def test_sustained_high_import_converges_faster_in_closed_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay a stable 2 kW load through meter, acknowledgements and actuator."""
    runtime, hass = runtime_pair()
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    commands: list[tuple[float, float]] = []
    grid = hass.states.get("sensor.grid")
    inverter = hass.states.get("number.m200")
    origin = datetime.now(UTC)
    monkeypatch.setattr(module, "utcnow", lambda: origin + timedelta(seconds=clock[0] - 1000))
    stamp = module.utcnow()

    async def acknowledge(domain, service, data, **kwargs):
        assert domain == "number" and service == "set_value"
        if data["entity_id"] == "number.m200":
            inverter.state = str(data["value"])
            inverter.last_reported = stamp + timedelta(microseconds=1)
            commands.append((clock[0], data["value"]))

    hass.services.async_call.side_effect = acknowledge
    # Strictly post-settle samples arrive on the next five-second tick.
    for tick in range(61):
        clock[0] = 1000.0 + tick * 5
        stamp = module.utcnow()
        grid.state = str(2000.0 - float(inverter.state) * 20)
        grid.last_reported = stamp
        await runtime.async_update()

    assert commands[0][1] <= 0.1
    assert all(b[1] - a[1] <= 1.00001 for a, b in pairwise(commands))
    assert 2000.0 - float(inverter.state) * 20 < 1500.0


@pytest.mark.asyncio
@pytest.mark.parametrize("verified, high_load", [(True, True), (False, True), (True, False)])
async def test_rounded_readback_can_progress_after_sustained_import(
    verified: bool, high_load: bool
) -> None:
    """Recorded 5.1% command / 5.09% readback permits a verified bounded step."""
    runtime, hass = runtime_pair()
    entity_id = "number.m200"
    runtime._last_sent[entity_id] = 5.1
    runtime._sent_at[entity_id] = 1000.0
    hass.states.get(entity_id).state = "5.09"
    runtime._hardware_feedback_matches = AsyncMock(return_value=verified)
    for i in range(3):
        runtime._grid_filter.observe(1100.0 if high_load else 100.0, i)
    runtime._grid_raw_w = 1100.0 if high_load else 100.0

    await runtime._apply({entity_id: 540}, now=1010.0)

    if high_load:
        runtime._hardware_feedback_matches.assert_awaited_once_with(entity_id, 5.1)
    if verified and high_load:
        assert hass.services.async_call.await_args.args[2]["value"] == 6.1
    else:
        hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_meter_sample_during_settling_cannot_release_next_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, hass = runtime_pair()
    dispatched = datetime.now(UTC)
    monkeypatch.setattr(module, "utcnow", lambda: dispatched)
    await runtime._apply({"number.m200": 520}, now=1000)
    inverter = hass.states.get("number.m200")
    inverter.state = "0.1"
    inverter.last_reported = dispatched + timedelta(seconds=1)
    runtime._hardware_feedback_matches = AsyncMock(return_value=True)

    assert not await runtime._feedback_ready(1010, dispatched + timedelta(seconds=1))
    assert runtime.status == "waiting_meter"
    assert runtime._pending
    assert await runtime._feedback_ready(1010, dispatched + timedelta(seconds=6))
    assert not runtime._pending


@pytest.mark.asyncio
async def test_out_of_order_grid_cannot_acknowledge_command() -> None:
    runtime, hass = runtime_pair()
    grid = hass.states.get("sensor.grid")
    inverter = hass.states.get("number.m200")
    t3 = datetime.now(UTC)
    t1 = t3 - timedelta(seconds=2)
    t2 = t3 - timedelta(seconds=1)
    runtime._grid_filter.observe(1100, t3)
    grid.last_reported = t1
    await runtime._apply({"number.m200": 520}, now=1000)
    inverter.state = "0.1"
    inverter.last_reported = datetime.now(UTC) + timedelta(seconds=1)
    runtime._hardware_feedback_matches = AsyncMock(return_value=True)

    assert not await runtime._feedback_ready(1010, t2)
    assert runtime.status == "waiting_meter"


@pytest.mark.asyncio
async def test_out_of_order_grid_does_not_suppress_unacknowledged_command_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, hass = runtime_pair()
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    grid = hass.states.get("sensor.grid")
    t3 = datetime.now(UTC)
    grid.last_reported = t3
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 0.1

    grid.last_reported = t3 - timedelta(seconds=1)
    clock[0] = 1031.0
    await runtime.async_update()

    assert runtime.status == "write_error"
    assert "not acknowledged" in runtime.last_error
    assert hass.services.async_call.await_args.args[2]["value"] == 0


@pytest.mark.asyncio
async def test_missing_grid_does_not_hide_unacknowledged_command_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime, hass = runtime_pair()
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 0.1

    hass.states.get("sensor.grid").state = "unavailable"
    clock[0] = 1031.0
    await runtime.async_update()

    assert runtime.status == "write_error"
    assert "not acknowledged" in runtime.last_error
    assert hass.services.async_call.await_args.args[2]["value"] == 0
    assert "number.m200" in runtime._shutdown_obligations


@pytest.mark.asyncio
async def test_missing_grid_does_not_hide_second_actuator_timeout() -> None:
    runtime, hass = runtime_pair()
    runtime._hardware_feedback_matches = AsyncMock(return_value=True)
    await runtime._apply({"number.m200": 520, "number.m80": 500}, now=1000)
    assert len(runtime._pending) == 2
    inverter = hass.states.get("number.m200")
    inverter.state = "0.1"
    inverter.last_reported = datetime.now(UTC) + timedelta(seconds=1)

    assert not await runtime._feedback_ready(1031, None)
    assert runtime.status == "write_error"
    assert "number.m80" in runtime.last_error
    assert "number.m80" in runtime._shutdown_obligations
