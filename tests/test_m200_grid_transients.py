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
    assert runtime._startup_export_trip is False
    assert "Excess grid export" not in (runtime.last_error or "")


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
