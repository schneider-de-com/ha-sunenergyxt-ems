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
    monkeypatch.setattr(
        module.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None)
    )
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
    assert any(
        call.args[2]["value"] == 0 for call in hass.services.async_call.await_args_list
    ), "Repeated export must still lower the power limit promptly"
