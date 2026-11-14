"""Backtest the Nov 14 recorded Deye AC outage against the MVP voltage gate.

This replays only the observed HA state transitions. The inverter-limit
values are cached control-register readings, NOT measured AC output.
No simulated plant response, saving estimate or actual hardware command.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")

from custom_components.sunenergyxt_ems import runtime as module  # noqa: E402
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "dual_deye_ac_loss_20261114.json"


def sample(value: object, unit: str, at: datetime, **attrs: object) -> SimpleNamespace:
    return SimpleNamespace(
        state=str(value),
        attributes={"unit_of_measurement": unit, **attrs},
        last_reported=at,
        last_updated=at,
    )


@pytest.mark.asyncio
async def test_recorded_ac_loss_never_permits_positive_dispatch_after_both_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    incident = json.loads(FIXTURE.read_text(encoding="utf-8"))
    tracks = incident["tracks"]
    anchor = datetime.fromisoformat(incident["time_origin_local"])
    events = sorted(
        (seconds, channel, value)
        for channel, rows in tracks.items()
        for seconds, value in rows
    )
    assert len(tracks["m200_limit_pct"]) >= 150
    assert len(tracks["m80_limit_pct"]) >= 40

    states = {
        "sensor.grid": sample(300, "W", anchor),
        "number.m200": sample(0, "%", anchor, min=0, max=100, step=0.1),
        "number.m80": sample(0, "%", anchor, min=0, max=100, step=0.1),
        "sensor.m200_voltage": sample(226, "V", anchor),
        "sensor.m80_voltage": sample(224, "V", anchor),
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock()),
    )
    entry = SimpleNamespace(
        entry_id="nov14-replay",
        data={
            "grid": "sensor.grid",
            "invert_grid": False,
            "batteries": [
                {
                    "device_id": "bk1",
                    "soc": 85,
                    "lower": 10,
                    "upper": 100,
                    "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
                },
                {
                    "device_id": "bk2",
                    "soc": 66,
                    "lower": 10,
                    "upper": 100,
                    "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
                },
            ],
        },
        options={"enabled": True, "feed_in": False, "grid_target_w": 20},
    )
    monkeypatch.setattr(module, "registered_connection", lambda *_: None)
    monkeypatch.setattr(module, "registered_dc_output", lambda *_: None)
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *_: None)
    monkeypatch.setattr(
        module.er, "async_get", lambda _: SimpleNamespace(async_get=lambda _: None)
    )
    mapping = {
        "number.m200": "sensor.m200_voltage",
        "number.m80": "sensor.m80_voltage",
    }
    monkeypatch.setattr(module, "registered_ac_voltage", lambda _h, key: mapping.get(key))
    runtime = EMSRuntime(hass, entry)
    runtime._running = True
    runtime._apply = AsyncMock()
    runtime._save_shutdown_obligations = AsyncMock(return_value=True)
    runtime._verify_shutdown = AsyncMock()

    channels = {
        "m200_grid_voltage_v": ("sensor.m200_voltage", "V"),
        "m80_grid_voltage_v": ("sensor.m80_voltage", "V"),
        "m200_limit_pct": ("number.m200", "%"),
        "m80_limit_pct": ("number.m80", "%"),
    }
    loss_at = {}
    contradictory = {"m200": 0, "m80": 0}
    active = True
    for seconds, channel, value in events:
        now = anchor + timedelta(seconds=seconds)
        monkeypatch.setattr(module, "utcnow", lambda now=now: now)
        if channel == "ems_enabled":
            active = bool(value)
            continue
        entity, unit = channels[channel]
        if unit == "%":
            states[entity] = sample(value, unit, now, min=0, max=100, step=0.1)
        else:
            states[entity] = sample(value, unit, now)
        if channel.endswith("voltage_v") and value == 0:
            loss_at[entity] = now
        if channel.endswith("limit_pct") and value > 0 and active:
            key = "m200" if "m200" in channel else "m80"
            if key == "m200" and now >= loss_at.get("sensor.m200_voltage", now + timedelta(days=1)):
                contradictory[key] += 1
            if key == "m80" and now >= loss_at.get("sensor.m80_voltage", now + timedelta(days=1)):
                contradictory[key] += 1
        if len(loss_at) == 2:
            batteries = runtime._batteries()
            assert all(not inv.online for b in batteries for inv in b.inverters), (
                "After recorded AC loss, a reachable Solarman number is never output-ready"
            )
    assert contradictory["m200"] >= 100
    assert contradictory["m80"] >= 10
    assert abs(
        (loss_at["sensor.m200_voltage"] - loss_at["sensor.m80_voltage"]).total_seconds()
    ) < 1, "The actual two voltage transitions occurred within one second"

    # Re-enter a lost-AC point with a fresh grid state and positive cached
    # number limits: runtime must dispatch only a minimum on both devices.
    final_at = max(loss_at.values()) + timedelta(seconds=1)
    monkeypatch.setattr(module, "utcnow", lambda: final_at)
    states["sensor.grid"] = sample(300, "W", final_at)
    await runtime.async_update()
    assert any(
        call.kwargs.get("emergency") is True
        and call.args[0] == {"number.m200": 0, "number.m80": 0}
        for call in runtime._apply.await_args_list
    )
    assert all(
        not any(value > 0 for value in call.args[0].values())
        for call in runtime._apply.await_args_list
    ), "No positive output may be requested while both AC supplies are absent"
