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

    for entity_id in ("number.m200", "number.m80"):
        inverter = hass.states.get(entity_id)
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
async def test_shutdown_ack_timeout_retries_once_and_preserves_interlock(monkeypatch):
    """Retry a missed acknowledgement without flooding a reachable inverter."""
    hass, entry = configured_two_bk215()
    runtime = EMSRuntime(hass, entry)
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    await runtime.async_disable()
    runtime._running = True
    for _ in range(4):
        clock[0] += 35
        await runtime.async_update()
    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert len(commands) == 4
    assert all(command["value"] == 0 for command in commands)
    assert runtime.status == "write_error"
    assert runtime._shutdown_obligations == {"number.m200", "number.m80"}


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


@pytest.mark.asyncio
async def test_manual_disable_writes_safe_minimum_to_all_reachable_inverters() -> None:
    """User disable must command both inverters, including one already reporting zero."""
    hass, entry = configured_two_bk215()
    runtime = EMSRuntime(hass, entry)
    await runtime.async_disable()
    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert {command["entity_id"] for command in commands} == {
        "number.m200",
        "number.m80",
    }
    assert all(command["value"] == 0 for command in commands)
    assert runtime.status == "shutdown_pending"


@pytest.mark.asyncio
async def test_stop_does_not_erase_other_inverter_failure_when_ack_is_pending() -> None:
    """One reachable inverter cannot hide a second inverter's shutdown failure."""
    hass, entry = configured_two_bk215()
    hass.states.get("number.m200").state = "unavailable"
    runtime = EMSRuntime(hass, entry)
    await runtime.async_disable()
    assert runtime.status == "write_error"
    assert "number.m200" in (runtime.last_error or "")
    entry.options["enabled"] = False
    runtime._running = True
    await runtime.async_update()
    assert runtime.status == "write_error"
    assert "number.m200" in (runtime.last_error or "")


@pytest.mark.asyncio
async def test_disabled_ems_sends_deferred_zero_when_offline_m80_recovers() -> None:
    """Unreachable at stop must not mean the later recovering M80 keeps its old limit."""
    hass, entry = configured_two_bk215()
    m80 = hass.states.get("number.m80")
    m80.state = "unavailable"
    runtime = EMSRuntime(hass, entry)
    await runtime.async_disable()
    commands = [c.args[2] for c in hass.services.async_call.await_args_list]
    assert all(c["entity_id"] != "number.m80" for c in commands)
    assert runtime.status == "write_error"

    m80.state = "100"
    m80.last_reported += timedelta(seconds=10)
    entry.options["enabled"] = False
    runtime._running = True
    await runtime.async_update()
    commands = [c.args[2] for c in hass.services.async_call.await_args_list]
    assert any(c["entity_id"] == "number.m80" and c["value"] == 0 for c in commands), (
        "A delayed zero must be sent once communication returns"
    )
    assert runtime.controller.enabled is False


@pytest.mark.asyncio
async def test_unconfirmed_offline_shutdown_blocks_controller_reenable() -> None:
    """Do not increase an unrelated inverter while another shutdown is unresolved."""
    hass, entry = configured_two_bk215()
    hass.states.get("number.m80").state = "unavailable"
    runtime = EMSRuntime(hass, entry)
    await runtime.async_disable()
    hass.services.async_call.reset_mock()
    entry.options["enabled"] = True
    runtime._running = True
    await runtime.async_update()
    assert runtime.status == "write_error"
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_unconfirmed_shutdown_survives_restart_and_is_retried() -> None:
    """A missed safe command remains outstanding through an HA restart."""
    hass, entry = configured_two_bk215()
    entry.options["enabled"] = False
    store_data = {}

    class Store:
        async def async_load(self):
            return dict(store_data)

        async def async_save(self, data):
            store_data.clear()
            store_data.update(data)

    hass.states.get("number.m80").state = "unavailable"
    first = EMSRuntime(hass, entry)
    first._shutdown_store = Store()
    await first.async_disable()
    assert "number.m80" in store_data["pending"]

    hass.services.async_call.reset_mock()
    m80 = hass.states.get("number.m80")
    m80.state = "100"
    restarted = EMSRuntime(hass, entry)
    restarted._shutdown_store = Store()
    await restarted._load_shutdown_obligations()
    restarted._running = True
    await restarted.async_update()
    commands = [c.args[2] for c in hass.services.async_call.await_args_list]
    assert any(c["entity_id"] == "number.m80" and c["value"] == 0 for c in commands)
    assert "number.m80" in store_data["pending"], "Dispatch is not acknowledgement"
    m80.state = "0"
    m80.last_reported += timedelta(seconds=1)
    await restarted.async_update()
    assert "number.m80" not in store_data["pending"]


@pytest.mark.asyncio
async def test_failed_shutdown_write_is_persisted_for_retry() -> None:
    """An online actuator whose service call fails is still owed a safe command."""
    hass, entry = configured_two_bk215()
    hass.services.async_call.side_effect = OSError("offline")
    store_data = {}

    class Store:
        async def async_save(self, data):
            store_data.update(data)

        async def async_load(self):
            return dict(store_data)

    runtime = EMSRuntime(hass, entry)
    runtime._shutdown_store = Store()
    await runtime.async_disable()
    assert {"number.m200", "number.m80"} <= set(store_data["pending"])


@pytest.mark.asyncio
async def test_reenable_is_blocked_until_all_shutdowns_are_acknowledged() -> None:
    """An issued but unconfirmed safe-off cannot be followed by regulation."""
    hass, entry = configured_two_bk215()
    runtime = EMSRuntime(hass, entry)
    await runtime.async_disable()
    hass.services.async_call.reset_mock()
    entry.options["enabled"] = True
    runtime._running = True
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()
    assert runtime.status != "running"


@pytest.mark.asyncio
async def test_storage_failure_does_not_prevent_emergency_commands() -> None:
    """Even if HA cannot persist the obligation, try to stop reachable hardware."""
    hass, entry = configured_two_bk215()
    runtime = EMSRuntime(hass, entry)
    runtime._shutdown_store.async_save = AsyncMock(side_effect=OSError("disk full"))
    await runtime.async_disable()
    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert {item["entity_id"] for item in commands} == {"number.m200", "number.m80"}
    assert runtime.status == "write_error"
    assert "persist" in (runtime.last_error or "").lower()


@pytest.mark.asyncio
async def test_shutdown_ack_is_not_forgotten_if_persistence_fails() -> None:
    """Failed persistence cannot release the interlock in the running process."""
    hass, entry = configured_two_bk215()
    runtime = EMSRuntime(hass, entry)
    await runtime.async_disable()
    for entity_id in ("number.m200", "number.m80"):
        state = hass.states.get(entity_id)
        state.state = "0"
        state.last_reported += timedelta(seconds=1)
    runtime._shutdown_store.async_save = AsyncMock(side_effect=OSError("disk full"))
    entry.options["enabled"] = True
    runtime._running = True
    await runtime.async_update()
    assert runtime._shutdown_obligations == {"number.m200", "number.m80"}
    assert runtime.status == "write_error"


@pytest.mark.parametrize("saved", [None, {}, {"pending": "number.m80"}])
@pytest.mark.asyncio
async def test_unknown_shutdown_state_requires_fresh_minimum_even_when_disabled(saved):
    """An absent or invalid journal must not let an inverter stay at 100%."""
    hass, entry = configured_two_bk215()
    entry.options["enabled"] = False
    runtime = EMSRuntime(hass, entry)
    runtime._shutdown_store.data = saved
    await runtime._load_shutdown_obligations()
    runtime._running = True
    await runtime.async_update()
    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert {item["entity_id"]: item["value"] for item in commands} == {
        "number.m200": 0,
        "number.m80": 0,
    }
    assert runtime.status == "shutdown_pending"


@pytest.mark.asyncio
async def test_confirmed_disabled_handover_does_not_override_manual_limit():
    hass, entry = configured_two_bk215()
    entry.options["enabled"] = False
    runtime = EMSRuntime(hass, entry)
    await runtime._shutdown_store.async_save({"pending": []})
    await runtime._load_shutdown_obligations()
    runtime._running = True
    runtime.status = "write_error"
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()
    assert runtime.status == "disabled"


@pytest.mark.asyncio
async def test_offline_then_reconnected_after_confirmed_handover_reissues_safe_zero() -> None:
    """A cleared shutdown journal must not hide a subsequent device reconnect."""
    hass, entry = configured_two_bk215()
    entry.options["enabled"] = False
    runtime = EMSRuntime(hass, entry)
    await runtime._shutdown_store.async_save({"pending": []})
    await runtime._load_shutdown_obligations()
    runtime._running = True

    # A deliberate manual setting while EMS is off is not overwritten.
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()
    assert runtime.status == "disabled"

    m80 = hass.states.get("number.m80")
    m80.state = "unavailable"
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()

    # A freshly reconnected inverter can restore its previous 100% setting.
    m80.state = "100"
    m80.last_reported += timedelta(seconds=1)
    await runtime.async_update()

    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert any(
        command["entity_id"] == "number.m80" and command["value"] == 0 for command in commands
    ), "Reconnection must initiate a fresh, verified emergency minimum"
    assert "number.m80" in runtime._shutdown_obligations
    assert runtime.status == "shutdown_pending"


@pytest.mark.asyncio
async def test_reconnect_reissues_zero_even_if_optimistic_entity_reports_zero() -> None:
    """A 0% entity value is not proof of a safe physical Solarman register."""
    hass, entry = configured_two_bk215()
    entry.options["enabled"] = False
    runtime = EMSRuntime(hass, entry)
    await runtime._shutdown_store.async_save({"pending": []})
    await runtime._load_shutdown_obligations()
    runtime._running = True

    await runtime.async_update()
    m80 = hass.states.get("number.m80")
    m80.state = "unavailable"
    await runtime.async_update()
    m80.state = "0"
    m80.last_reported += timedelta(seconds=1)
    await runtime.async_update()

    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert any(
        command["entity_id"] == "number.m80" and command["value"] == 0 for command in commands
    ), "Reconnect requires a new write/readback even with optimistic 0%"


@pytest.mark.asyncio
async def test_solarman_connection_off_on_reconnect_is_safe_when_number_never_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay 2026-11-03 14:40:17-22: connection off/on; M80 0 -> 100%."""
    hass, entry = configured_two_bk215()
    entry.options["enabled"] = False
    old_state = hass.states.get
    connection = sample("on", "boolean")
    hass.states.get = lambda key: (
        connection if key == "binary_sensor.link" else old_state(key)
    )
    monkeypatch.setattr(
        module,
        "registered_connection",
        lambda _hass, entity: "binary_sensor.link" if entity == "number.m80" else None,
        raising=False,
    )
    runtime = EMSRuntime(hass, entry)
    await runtime._shutdown_store.async_save({"pending": []})
    await runtime._load_shutdown_obligations()
    runtime._running = True

    m80 = hass.states.get("number.m80")
    m80.state = "0"
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()

    connection.state = "off"
    connection.last_reported += timedelta(seconds=1)
    # Actual 2026-11-03 M80 number never became unavailable.
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()

    connection.state = "on"
    connection.last_reported += timedelta(seconds=1)
    m80.state = "100"
    m80.last_reported += timedelta(seconds=1)
    await runtime.async_update()

    commands = [call.args[2] for call in hass.services.async_call.await_args_list]
    assert any(
        command["entity_id"] == "number.m80" and command["value"] == 0 for command in commands
    ), "A Solarman connection off/on must not leave a restored 100% limit"
    assert "number.m80" in runtime._shutdown_obligations
