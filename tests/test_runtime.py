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
        "batteries": [
            {
                "device_id": "battery-1",
                "soc": "sensor.soc",
                "lower": "number.lower",
                "upper": "number.upper",
                "inverters": [{"entity_id": "number.inverter", "rated_w": 800}],
            }
        ],
    }
    states = {
        "sensor.grid": sample(grid, "W", seconds_old=old),
        "sensor.soc": sample(soc),
        "number.lower": sample(lower),
        "number.upper": sample(90),
        "number.inverter": sample(
            0, inverter_unit, min=0, max=100 if inverter_unit == "%" else 800, step=1
        ),
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
    assert hass.services.async_call.await_args.args[2]["value"] == 200


@pytest.mark.asyncio
async def test_positive_percent_limit_is_converted_from_watts():
    hass, entry = configured(inverter_unit="%")
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 25


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


@pytest.mark.asyncio
async def test_timer_runs_updates_on_event_loop_without_thread_unsafe_task(monkeypatch):
    """HA timer must receive a coroutine, not a synchronous executor callback."""
    from inspect import iscoroutinefunction
    from unittest.mock import Mock

    hass, entry = configured()
    cancel = Mock()
    captured = []

    def register(hass_instance, handler, interval):
        assert hass_instance is hass
        assert interval == module.UPDATE_PERIOD
        assert iscoroutinefunction(handler), "Sync handlers run in HA executor threads"
        captured.append(handler)
        return cancel

    monkeypatch.setattr(module, "async_track_time_interval", register)
    runtime = EMSRuntime(hass, entry)
    real_update = runtime.async_update
    wrapped = AsyncMock(wraps=real_update)
    monkeypatch.setattr(runtime, "async_update", wrapped)

    await runtime.async_start()
    assert runtime._running
    assert wrapped.await_count == 1

    await captured[0](datetime.now(UTC))
    await captured[0](datetime.now(UTC))
    assert wrapped.await_count == 3

    await runtime.async_stop()
    cancel.assert_called_once()
    assert not runtime._running


def test_negative_numeric_helpers_reject_missing_bad_and_nonfinite_inputs():
    hass, entry = configured()
    runtime = EMSRuntime(hass, entry)
    assert runtime._float(None) is None
    assert runtime._float("sensor.not_found") is None
    assert runtime._float(float("inf")) is None
    assert runtime._float("nonsense") is None
    assert runtime._float("42.5") == 42.5


def test_negative_invalid_inverter_rating_is_skipped():
    hass, entry = configured()
    entry.data["batteries"][0]["inverters"][0]["rated_w"] = "invalid"
    runtime = EMSRuntime(hass, entry)
    assert runtime._batteries()[0].inverters == ()


def test_positive_grid_kilowatts_are_converted_and_sign_can_be_reversed():
    hass, entry = configured()
    entry.data["invert_grid"] = True
    state = hass.states.get("sensor.grid")
    state.state = "0.5"
    state.attributes["unit_of_measurement"] = "kW"
    runtime = EMSRuntime(hass, entry)
    value, sequence = runtime._grid()
    assert value == -500
    assert sequence is not None


def test_negative_missing_grid_state_returns_no_sample():
    hass, entry = configured()
    old_get = hass.states.get
    hass.states.get = lambda key: None if key == "sensor.grid" else old_get(key)
    runtime = EMSRuntime(hass, entry)
    assert runtime._grid() == (None, None)


def test_negative_wrong_grid_unit_has_no_valid_sample():
    hass, entry = configured()
    hass.states.get("sensor.grid").attributes["unit_of_measurement"] = "kWh"
    assert EMSRuntime(hass, entry)._grid() == (None, None)


def test_positive_ap_systems_inverters_use_conservative_command_interval(monkeypatch):
    hass, entry = configured()
    monkeypatch.setattr(
        module.er,
        "async_get",
        lambda _: SimpleNamespace(async_get=lambda entity: SimpleNamespace(platform="apsystems")),
    )
    runtime = EMSRuntime(hass, entry)
    assert runtime._minimum_interval("number.inverter") == 300


@pytest.mark.asyncio
async def test_negative_unavailable_limit_is_not_written():
    hass, entry = configured()
    hass.states.get("number.inverter").state = "unavailable"
    runtime = EMSRuntime(hass, entry)
    await runtime._apply({"number.inverter": 100}, now=50)
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_malformed_limit_unit_is_not_written():
    hass, entry = configured()
    hass.states.get("number.inverter").attributes["unit_of_measurement"] = "kWh"
    runtime = EMSRuntime(hass, entry)
    await runtime._apply({"number.inverter": 100}, now=50)
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_invalid_inverter_limits_are_not_written():
    hass, entry = configured()
    hass.states.get("number.inverter").attributes["min"] = 900
    runtime = EMSRuntime(hass, entry)
    await runtime._apply({"number.inverter": 100}, now=50)
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_positive_no_new_command_for_tiny_limit_changes():
    hass, entry = configured()
    runtime = EMSRuntime(hass, entry)
    runtime._last_sent["number.inverter"] = 400
    await runtime._apply({"number.inverter": 404}, now=1000)
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_negative_controller_does_not_write_if_disabled():
    hass, entry = configured()
    entry.options["enabled"] = False
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    hass.services.async_call.assert_not_awaited()
    assert runtime.status == "disabled"


@pytest.mark.asyncio
async def test_positive_disabling_through_options_stops_inverters():
    hass, entry = configured()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    entry.options["enabled"] = False
    hass.services.async_call.reset_mock()
    await runtime.async_change_options()
    assert not runtime.controller.enabled
    assert hass.services.async_call.await_args.args[2]["value"] == 0


@pytest.mark.asyncio
async def test_positive_reenabling_through_options_resumes_if_data_valid():
    hass, entry = configured()
    entry.options["enabled"] = False
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    entry.options["enabled"] = True
    await runtime.async_change_options()
    assert runtime.controller.enabled
    hass.services.async_call.assert_awaited()
    assert runtime.status == "waiting_feedback"


@pytest.mark.asyncio
async def test_stale_soc_while_grid_is_fresh_stops_discharge():
    hass, entry = configured()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    hass.services.async_call.reset_mock()
    stale = hass.states.get("sensor.soc")
    stale.last_reported = datetime.now(UTC) - timedelta(minutes=2)
    await runtime.async_update()
    assert runtime.controller.state.blocked == {"battery-1"}
    assert hass.services.async_call.await_args.args[2]["value"] == 0


@pytest.mark.asyncio
async def test_advanced_grid_target_option_changes_output():
    hass, entry = configured(grid=420)
    entry.options["grid_target_w"] = 40
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 190


@pytest.mark.asyncio
async def test_household_feed_in_mode_ignores_upper_soc_for_normal_demand() -> None:
    """The source screenshot says household mode is independent of full charge."""
    hass, entry = configured(grid=100, soc=95)
    entry.options.update({"feed_in": True, "feed_in_mode": "household"})
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 40


@pytest.mark.asyncio
async def test_pv_feed_in_mode_uses_maximum_only_at_upper_soc() -> None:
    """The PV mode can release maximum power after reaching the upper SoC."""
    hass, entry = configured(grid=100, soc=95)
    entry.options.update({"feed_in": True, "feed_in_mode": "pv"})
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert hass.services.async_call.await_args.args[2]["value"] == 800


@pytest.mark.asyncio
async def test_reported_bk215_dc_overlimit_latches_safety_shutdown() -> None:
    """The manufacturer specifies 1920 W DC, not 1920 W of inverter AC setpoints."""
    hass, entry = configured(grid=3000, soc=85)
    entry.data["batteries"][0]["dc_output"] = "sensor.bk_dc"
    original_get = hass.states.get
    dc_state = sample(2077, "W")
    hass.states.get = lambda entity: dc_state if entity == "sensor.bk_dc" else original_get(entity)
    inverter = hass.states.get("number.inverter")
    inverter.state = "500"
    runtime = EMSRuntime(hass, entry)
    runtime._last_sent["number.inverter"] = 500
    runtime._running = True

    await runtime.async_update()
    assert runtime.status == "hardware_limit"
    assert hass.services.async_call.await_args.args[2]["value"] == 0

    dc_state.state = "0"
    dc_state.last_reported = datetime.now(UTC) + timedelta(seconds=1)
    hass.services.async_call.reset_mock()
    hass.states.get("sensor.grid").last_reported = datetime.now(UTC) + timedelta(seconds=1)
    await runtime.async_update()
    assert runtime.status == "hardware_limit", (
        "A transient low reading must not auto-release a DC trip"
    )
    hass.services.async_call.assert_not_awaited()
