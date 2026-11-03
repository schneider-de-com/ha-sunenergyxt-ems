"""Regression: a HACS upgrade must preserve BK215 DC monitoring."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import runtime as module  # noqa: E402
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402


def state(value, unit="W"):
    stamp = datetime.now(UTC)
    return SimpleNamespace(
        state=str(value),
        attributes={"unit_of_measurement": unit, "min": 0, "max": 100, "step": 0.1},
        last_reported=stamp,
        last_updated=stamp,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("dc,expected", [(2077, "hardware_limit"), ("unavailable", "dc_unavailable"), (0, "waiting_feedback")])
async def test_legacy_entry_recovers_dc_sensor(monkeypatch, dc, expected):
    states = {
        "sensor.grid": state(500),
        "sensor.soc": state(90, "%"),
        "number.lower": state(10, "%"),
        "number.upper": state(95, "%"),
        "number.m200": state(25, "%"),
        "sensor.bk_dc": state(dc),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
        config_entries=SimpleNamespace(async_update_entry=lambda entry, *, options: setattr(entry, "options", options)),
    )
    entry = SimpleNamespace(
        entry_id="legacy",
        options={"enabled": True, "feed_in": False},
        data={"grid": "sensor.grid", "batteries": [{
            "device_id": "bk1", "soc": "sensor.soc",
            "lower": "number.lower", "upper": "number.upper",
            "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
        }]},
    )
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    monkeypatch.setattr(module.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None, entities={}))
    monkeypatch.setattr(module, "battery_suggestions", lambda _: {"bk1": {"dc_output": "sensor.bk_dc"}}, raising=False)
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    await runtime.async_update()
    assert runtime.status == expected
    if dc != 0:
        assert hass.services.async_call.await_args.args[2]["value"] == 0
    if dc == 2077:
        assert entry.options["dc_tripped_batteries"] == ["bk1"]
