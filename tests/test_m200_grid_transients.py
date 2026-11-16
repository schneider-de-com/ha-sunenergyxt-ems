"""Regression contracts for the measured M200 startup/export oscillation (#27).

Use the real Home Assistant runtime API and a fake number service. The test is
RED against 0.1.0b4: one transient signed-grid sample currently sends 0%.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import runtime as module  # noqa: E402
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402


def sample(value: float, unit: str, **attributes: object) -> SimpleNamespace:
    stamp = datetime.now(UTC) - timedelta(seconds=1)
    return SimpleNamespace(
        state=str(value),
        attributes={"unit_of_measurement": unit, **attributes},
        last_reported=stamp,
        last_updated=stamp,
    )


def configured_runtime() -> tuple[SimpleNamespace, SimpleNamespace]:
    states = {
        "sensor.grid": sample(20, "W"),
        "sensor.soc": sample(89, "%"),
        "number.low": sample(10, "%"),
        "number.high": sample(100, "%"),
        "number.m200": sample(10, "%", min=0, max=100, step=0.1),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    entry = SimpleNamespace(
        entry_id="m200-startup-regression",
        data={
            "grid": "sensor.grid",
            "invert_grid": False,
            "batteries": [
                {
                    "device_id": "bk215-1",
                    "soc": "sensor.soc",
                    "lower": "number.low",
                    "upper": "number.high",
                    "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
                }
            ],
        },
        options={"enabled": True, "feed_in": False, "grid_target_w": 20},
    )
    return hass, entry


@pytest.fixture(autouse=True)
def no_external_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    monkeypatch.setattr(module.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None))
    monkeypatch.setattr(module, "registered_dc_output", lambda *_: None)


@pytest.mark.asyncio
async def test_one_measured_m200_export_pulse_does_not_restart_inverter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The one-sample export spike after 0->10% must not trigger new 0->10% cycling."""
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    meter = hass.states.get("sensor.grid")
    base = datetime.now(UTC) - timedelta(seconds=1)
    for i, watts in enumerate((20.0, 22.0, 19.0, -700.0, 420.0)):
        clock[0] += 5
        meter.state = str(watts)
        meter.last_reported = base + timedelta(milliseconds=100 * (i + 1))
        await runtime.async_update()
    hass.services.async_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_persistent_measured_export_still_reduces_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ignore an isolated surge, never ignore three consecutive genuine export readings."""
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [2000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    meter = hass.states.get("sensor.grid")
    base = datetime.now(UTC) - timedelta(seconds=1)
    for i, watts in enumerate((20.0, 22.0, 19.0, -700.0, -710.0, -720.0)):
        clock[0] += 5
        meter.state = str(watts)
        meter.last_reported = base + timedelta(milliseconds=100 * (i + 1))
        await runtime.async_update()
    assert any(call.args[2]["value"] == 0 for call in hass.services.async_call.await_args_list), (
        "Repeated export must still lower the power limit promptly"
    )


@pytest.mark.asyncio
async def test_measured_startup_export_bypasses_hold(monkeypatch):
    """Replay Shelly samples from the supervised run on 2026-11-04 11:19 CET."""
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    hass.states.get("number.m200").state = "6.3"
    runtime._start_hold.record("number.m200", before_w=0, after_w=126, now=1000)
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    meter = hass.states.get("sensor.grid")
    base = datetime.now(UTC) - timedelta(seconds=1)
    for i, watts in enumerate((273.132, 270.591, 225.843, -56.642, -522.527)):
        clock[0] += 5
        meter.state = str(watts)
        meter.last_reported = base + timedelta(milliseconds=100 * (i + 1))
        await runtime.async_update()
    hass.services.async_call.assert_awaited_once()
    assert hass.services.async_call.await_args.args[2]["value"] == 0
    assert "Excess grid export" in (runtime.last_error or "")


@pytest.mark.asyncio
async def test_direct_severe_export_from_positive_import_trips_during_startup_hold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Live replay 2026-11-04 12:14: a first severe export must trip immediately.

    A pre-export sample below -30 W is not guaranteed; L1 may swing
    straight from import into severe export before the next 5 s reading.
    """
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    hass.states.get("number.m200").state = "5.5"
    runtime._start_hold.record("number.m200", before_w=0, after_w=110, now=1000)
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    meter = hass.states.get("sensor.grid")
    base = datetime.now(UTC) - timedelta(seconds=1)
    for i, watts in enumerate((239.133, 153.136, -695.867)):
        clock[0] += 5
        meter.state = str(watts)
        meter.last_reported = base + timedelta(milliseconds=100 * (i + 1))
        await runtime.async_update()

    assert runtime._startup_export_trip is True
    assert "Excess grid export" in (runtime.last_error or "")
    assert any(
        call.args[2]["entity_id"] == "number.m200" and call.args[2]["value"] == 0
        for call in hass.services.async_call.await_args_list
    ), "One fresh severe export during startup must immediately request zero"


@pytest.mark.asyncio
async def test_severe_export_when_feed_in_enabled_does_not_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Intentional feed-in must not activate the zero-export startup latch."""
    hass, entry = configured_runtime()
    entry.options["feed_in"] = True
    entry.options["feed_in_mode"] = "pv"
    entry.options["pv_input"] = {"bk215-1": "sensor.pv"}
    states = hass.states.get.__self__
    states["sensor.pv"] = sample(450, "W")
    states["sensor.soc"].state = "100"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    # Previous controller cycle has entered PV-only full-charge mode.
    runtime.controller.state.full.add("bk215-1")
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    runtime._start_hold.record("number.m200", before_w=0, after_w=110, now=1000)
    meter = hass.states.get("sensor.grid")
    meter.state = "-695.867"
    meter.last_reported = datetime.now(UTC) - timedelta(seconds=1)

    await runtime.async_update()

    assert runtime.controller.feed_in is True
    assert runtime._startup_export_trip is False
    assert "Excess grid export" not in (runtime.last_error or "")


@pytest.mark.asyncio
async def test_pv_mode_selected_but_not_full_must_retain_startup_export_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The enabled mode alone is not evidence of intentional PV export."""
    hass, entry = configured_runtime()
    entry.options.update({"feed_in": True, "feed_in_mode": "pv"})
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    runtime._start_hold.record("number.m200", before_w=0, after_w=110, now=1000)
    meter = hass.states.get("sensor.grid")
    meter.state = "-695.867"
    meter.last_reported = datetime.now(UTC) - timedelta(seconds=1)

    await runtime.async_update()

    assert runtime.controller.feed_in is True
    assert runtime._startup_export_trip is True


@pytest.mark.asyncio
async def test_severe_export_outside_start_hold_does_not_latch_startup_fault(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Normal export feedback remains separate from startup-only emergency latch."""
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    monkeypatch.setattr(module, "monotonic", lambda: 1000.0)
    meter = hass.states.get("sensor.grid")
    meter.state = "-695.867"
    meter.last_reported = datetime.now(UTC) - timedelta(seconds=1)

    await runtime.async_update()

    assert runtime._startup_export_trip is False
    assert "Excess grid export during startup" not in (runtime.last_error or "")


@pytest.mark.asyncio
async def test_changed_raw_value_without_new_meter_report_does_not_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reject a stale/synthetic export value without a distinct meter timestamp."""
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    runtime._start_hold.record("number.m200", before_w=0, after_w=110, now=1000)
    meter = hass.states.get("sensor.grid")
    stamp = datetime.now(UTC) - timedelta(seconds=1)
    meter.state = "239.133"
    meter.last_reported = stamp
    meter.last_updated = stamp

    await runtime.async_update()
    assert runtime._startup_export_trip is False

    clock[0] += 5
    meter.state = "-695.867"
    # HA has not published an actual fresh meter sample.
    await runtime.async_update()

    assert runtime._startup_export_trip is False
    assert "Excess grid export during startup" not in (runtime.last_error or "")


@pytest.mark.asyncio
async def test_real_m200_one_percent_export_trips_startup_without_second_negative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Replay 2026-11-04 16:52: nominal 20 W command, -188.632 W net export.

    A 1% limit is not a 20 W AC measurement on the real M200. The first
    fresh severe net-export reading must trigger a fail-closed reduction.
    """
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    hass.states.get("number.m200").state = "1"
    runtime._start_hold.record("number.m200", before_w=0, after_w=20, now=1000)
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    meter = hass.states.get("sensor.grid")
    base = datetime.now(UTC) - timedelta(seconds=1)
    for i, value in enumerate((250.118, -188.632)):
        clock[0] += 5
        meter.state = str(value)
        meter.last_reported = base + timedelta(milliseconds=100 * (i + 1))
        await runtime.async_update()

    assert runtime._startup_export_trip is True
    assert "Excess grid export" in (runtime.last_error or "")
    assert any(
        call.args[2] == {"entity_id": "number.m200", "value": 0}
        for call in hass.services.async_call.await_args_list
    ), "The first -188.632 W sample must stop the reachable inverter"


@pytest.mark.asyncio
async def test_small_minus_49_w_startup_sample_not_latched_as_severe_export(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not turn routine small deviations into a permanent safety latch."""
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    hass.states.get("number.m200").state = "1"
    runtime._start_hold.record("number.m200", before_w=0, after_w=20, now=1000)
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    meter = hass.states.get("sensor.grid")
    meter.state = "-49.610"
    meter.last_reported = datetime.now(UTC) - timedelta(seconds=1)
    await runtime.async_update()

    assert runtime._startup_export_trip is False
    assert "Excess grid export" not in (runtime.last_error or "")


def test_two_fresh_export_samples_bypass_median_but_one_does_not() -> None:
    """Replay the November 5 load drop without reacting to an isolated spike."""
    meter_filter = module.GridMedianFilter()
    for i, value in enumerate((1503.0, 1487.0, 539.0)):
        meter_filter.observe(value, i)
    meter_filter.observe(-479.0, 3)
    assert meter_filter.confirmed_export() is None
    # Reobserving a single sensor report cannot manufacture confirmation.
    meter_filter.observe(-479.0, 3)
    assert meter_filter.confirmed_export() is None
    meter_filter.observe(-486.0, 4)
    assert meter_filter.confirmed_export() == -479.0
    meter_filter.observe(200.0, 5)
    assert meter_filter.confirmed_export() is None
    meter_filter.reset()
    assert meter_filter.confirmed_export() is None


@pytest.mark.asyncio
async def test_repeated_export_reduces_before_five_sample_median(monkeypatch):
    """The second fresh negative sample must reach control before the median."""
    hass, entry = configured_runtime()
    hass.states.get("number.m200").state = "40.1"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    monkeypatch.setattr(runtime, "_apply", AsyncMock())
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    meter = hass.states.get("sensor.grid")
    base = datetime.now(UTC) - timedelta(seconds=1)
    for i, watts in enumerate((1503.0, 1487.0, 539.0, -479.0, -486.0)):
        clock[0] += 5
        meter.state = str(watts)
        meter.last_reported = base + timedelta(milliseconds=100 * (i + 1))
        await runtime.async_update()
    assert runtime._grid_filtered_w == -479.0
    assert runtime._apply.await_args.args[0]["number.m200"] < 802.0


@pytest.mark.asyncio
async def test_confirmed_export_does_not_override_intentional_feed_in(monkeypatch):
    """The faster feedback path must not override explicit feed-in mode."""
    hass, entry = configured_runtime()
    entry.options["feed_in"] = True
    entry.options["feed_in_mode"] = "pv"
    entry.options["pv_input"] = {"bk215-1": "sensor.pv"}
    states = hass.states.get.__self__
    states["sensor.pv"] = sample(350, "W")
    states["sensor.soc"].state = "100"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    runtime.controller.state.full.add("bk215-1")
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    meter = hass.states.get("sensor.grid")
    base = datetime.now(UTC) - timedelta(seconds=1)
    for i, watts in enumerate((1503.0, 1487.0, 539.0, -479.0, -486.0)):
        clock[0] += 5
        meter.state = str(watts)
        meter.last_reported = base + timedelta(milliseconds=100 * (i + 1))
        await runtime.async_update()
    assert runtime._grid_filtered_w == 539.0


@pytest.mark.asyncio
async def test_startup_export_trip_self_recovers_after_confirmed_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """2026-11-15 16:35:36: emergency zero must not require a manual OFF/ON reset.

    Three distinct fresh positive Shelly reports and completed inverter feedback
    permit automatic re-entry; emergency zero is still sent at the first severe
    startup export. Replays a real failure that persisted until 21:20.
    """
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    runtime._start_hold.record("number.m200", before_w=0, after_w=20, now=1000)
    dispatched = AsyncMock()
    monkeypatch.setattr(runtime, "_apply", dispatched)
    monkeypatch.setattr(runtime, "_feedback_ready", AsyncMock(return_value=True))
    meter = hass.states.get("sensor.grid")
    stamp = datetime.now(UTC) - timedelta(seconds=1)

    for watts, sequence, elapsed in [
        (-1362.715, 1, 1005.0),
        (350.903, 2, 1010.0),
        (351.172, 3, 1020.0),
        (357.379, 4, 1040.0),
    ]:
        clock[0] = elapsed
        meter.state = str(watts)
        meter.last_reported = stamp + timedelta(milliseconds=sequence)
        await runtime.async_update()
        if sequence == 1:
            assert runtime._startup_export_trip is True

    assert any(call.args[0] == {"number.m200": 0.0} for call in dispatched.await_args_list), (
        "The first severe export must still request the safe minimum"
    )
    assert runtime._startup_export_trip is False, (
        "Stable fresh import after the confirmed reduction must clear the temporary trip"
    )
    assert runtime.status != "write_error"
    assert "switch EMS off" not in (runtime.last_error or "")


@pytest.mark.asyncio
async def test_startup_export_recovery_requires_distinct_meter_reports_and_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Do not auto-restart on a repeated Shelly timestamp or an unconfirmed zero."""
    hass, entry = configured_runtime()
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    runtime._start_hold.record("number.m200", before_w=0, after_w=20, now=1000)
    monkeypatch.setattr(runtime, "_apply", AsyncMock())
    monkeypatch.setattr(runtime, "_feedback_ready", AsyncMock(return_value=False))
    meter = hass.states.get("sensor.grid")
    stamp = datetime.now(UTC) - timedelta(seconds=1)

    clock[0] = 1005
    meter.state = "-188.632"
    meter.last_reported = stamp + timedelta(milliseconds=1)
    await runtime.async_update()
    assert runtime._startup_export_trip

    clock[0] = 1010
    meter.state = "300"
    meter.last_reported = stamp + timedelta(milliseconds=2)
    await runtime.async_update()

    # Advance scheduler without fresh meter updates; never count those as recovery.
    for now in (1020, 1030, 1040, 1050):
        clock[0] = now
        await runtime.async_update()
    assert runtime._startup_export_trip is True

    # Fresh positive reports exist, but verified device ACK is still outstanding.
    runtime._pending["number.m200"] = module.PendingCommand(
        target=0,
        entity_reported_before=stamp,
        sent_at=1005,
        grid_reported_before=stamp,
    )
    for idx, now in enumerate((1060, 1065, 1070), start=3):
        clock[0] = now
        meter.last_reported = stamp + timedelta(milliseconds=idx)
        await runtime.async_update()
    assert runtime._startup_export_trip is True, (
        "Fresh grid import alone cannot replace confirmed inverter shutdown"
    )


@pytest.mark.asyncio
async def test_startup_export_reduces_two_live_inverters_proportionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Q4: one fresh severe export reduces BOTH commanded limits before a second sample.

    A 1.36 kW net export is not a reason to force an already high 1.8 kW
    setpoint directly through Deye's risky 0%-to-positive startup transition.
    Dispatch limits are nominal, not a claim about actual AC generation.
    """
    hass, entry = configured_runtime()
    entry.data["batteries"][0]["inverters"].append({"entity_id": "number.m80", "rated_w": 800})
    old_get = hass.states.get
    m80 = sample(5, "%", min=0, max=100, step=0.1)
    hass.states.get = lambda key: m80 if key == "number.m80" else old_get(key)
    m200 = hass.states.get("number.m200")
    m200.state = "90"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    runtime._last_dispatched_w.update({"number.m200": 1800.0, "number.m80": 40.0})
    runtime._start_hold.record("number.m200", before_w=0, after_w=1800, now=1000)
    clock = [1000.0]
    monkeypatch.setattr(module, "monotonic", lambda: clock[0])
    dispatched = AsyncMock()
    monkeypatch.setattr(runtime, "_apply", dispatched)
    monkeypatch.setattr(runtime, "_feedback_ready", AsyncMock(return_value=False))
    meter = hass.states.get("sensor.grid")
    base = datetime.now(UTC) - timedelta(seconds=1)

    meter.state = "-1362.715"
    meter.last_reported = base + timedelta(milliseconds=100)
    clock[0] = 1005.0
    await runtime.async_update()
    first = dispatched.await_args.args[0]
    assert 0 < first["number.m200"] < 1800
    assert 0 < first["number.m80"] < 40
    assert sum(first.values()) == pytest.approx(1840 - (1362.715 + 20))
    assert runtime._startup_export_trip is True

    # Replay the first dispatch as the newly reported command baseline; the
    # next distinct negative report must request another DOWNWARD adjustment.
    runtime._last_dispatched_w.update(first)
    m200.state = str(first["number.m200"] / 20)
    m80.state = str(first["number.m80"] / 8)
    meter.state = "-150"
    meter.last_reported = base + timedelta(milliseconds=200)
    clock[0] = 1010.0
    await runtime.async_update()
    second = dispatched.await_args.args[0]
    assert 0 <= second["number.m200"] < first["number.m200"]
    assert 0 <= second["number.m80"] < first["number.m80"]
    assert sum(second.values()) == pytest.approx(sum(first.values()) - 170)
    assert dispatched.await_count == 2

    # A five-second scheduler cycle reusing the SAME meter timestamp cannot
    # spend the correction budget twice.
    clock[0] = 1015.0
    await runtime.async_update()
    assert dispatched.await_count == 2


@pytest.mark.asyncio
async def test_startup_export_with_no_verified_command_baseline_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On restart, an optimistic HA number is not proof of an EMS-issued limit."""
    hass, entry = configured_runtime()
    hass.states.get("number.m200").state = "90"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    runtime._start_hold.record("number.m200", before_w=0, after_w=1800, now=1000)
    monkeypatch.setattr(module, "monotonic", lambda: 1005.0)
    dispatched = AsyncMock()
    monkeypatch.setattr(runtime, "_apply", dispatched)
    monkeypatch.setattr(runtime, "_feedback_ready", AsyncMock(return_value=False))
    meter = hass.states.get("sensor.grid")
    meter.state = "-700"
    meter.last_reported = datetime.now(UTC) - timedelta(seconds=1)
    await runtime.async_update()
    assert dispatched.await_args.args[0] == {"number.m200": 0.0}


@pytest.mark.asyncio
async def test_role_change_does_not_force_healthy_previous_main_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A new preferred storage head must not cycle a still-eligible inverter.

    Previously an explicit emergency 0% reset was issued on the old main
    before allocating the shared output target. That creates avoidable
    high-power 0%-to-positive Deye startup transitions.
    """
    hass, entry = configured_runtime()
    entry.data["batteries"].append(
        {
            "device_id": "head-two",
            "soc": "sensor.soc2",
            "lower": 10,
            "upper": 100,
            "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
        }
    )
    old_get = hass.states.get
    extra = {
        "sensor.soc2": sample(90, "%"),
        "number.m80": sample(10, "%", min=0, max=100, step=0.1),
    }
    hass.states.get = lambda key: extra.get(key) or old_get(key)
    old_get("sensor.soc").state = "25"
    old_get("number.m200").state = "70"
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    runtime.controller.state.main = "bk215-1"
    runtime.controller.state.outputs = {"number.m200": 1400, "number.m80": 80}
    runtime._last_sent = {"number.m200": 70, "number.m80": 10}
    monkeypatch.setattr(runtime, "_feedback_ready", AsyncMock(return_value=True))
    dispatched = AsyncMock()
    monkeypatch.setattr(runtime, "_apply", dispatched)
    meter = old_get("sensor.grid")
    meter.state = "800"
    meter.last_reported = datetime.now(UTC) - timedelta(seconds=1)

    await runtime.async_update()

    assert runtime.controller.state.main == "head-two"
    dispatched.assert_awaited_once()
    targets = dispatched.await_args.args[0]
    assert targets["number.m200"] > 0
    assert targets["number.m80"] > 0
    assert not dispatched.await_args.kwargs.get("emergency", False)
    assert runtime.status != "switching_main"
