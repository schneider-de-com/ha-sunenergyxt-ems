"""Live-contract regressions: Solarman number state is an optimistic write, not a readback.

M200 and M80 with lookup_file=deye_micro_53.yaml were directly measured on
2026-11-04: a 1% HA command gave holding register 53 = 100, and a 0% command
gave register 53 = 0. These tests never communicate with real hardware.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import runtime as module  # noqa: E402
from custom_components.sunenergyxt_ems.runtime import EMSRuntime  # noqa: E402


@pytest.fixture
def solarman_pair(monkeypatch: pytest.MonkeyPatch):
    now = datetime.now(UTC)

    def state(value, unit="%"):
        return SimpleNamespace(
            state=str(value),
            attributes={"unit_of_measurement": unit, "min": 0, "max": 100, "step": 0.1},
            last_reported=now,
            last_updated=now,
        )

    states = {
        "sensor.grid": state(300, "W"),
        "sensor.soc1": state(80),
        "sensor.soc2": state(70),
        "number.low1": state(10),
        "number.low2": state(10),
        "number.high1": state(100),
        "number.high2": state(100),
        "number.m200": state(100),
        "number.m80": state(100),
    }
    hardware = {"device_m200": 10000, "device_m80": 10000}
    lookup_file = ["deye_micro_53.yaml"]

    async def fake_service(domain, service, data, **kwargs):
        if domain == "number" and service == "set_value":
            # Solarman's write() calls async_write_ha_state optimistically.
            entity = states[data["entity_id"]]
            entity.state = str(data["value"])
            entity.last_reported += timedelta(seconds=1)
            return None
        if domain == "solarman" and service == "read_holding_registers":
            assert data["address"] == 53 and data["count"] == 1
            assert kwargs.get("return_response") is True
            return {"53": hardware[data["device"]]}
        raise AssertionError(f"Unexpected service {domain}.{service}")

    hass = SimpleNamespace(
        states=SimpleNamespace(get=states.get),
        services=SimpleNamespace(async_call=AsyncMock(side_effect=fake_service)),
        config_entries=SimpleNamespace(
            async_get_entry=lambda _: SimpleNamespace(options={"lookup_file": lookup_file[0]})
        ),
    )
    registry = {
        entity: SimpleNamespace(
            platform="solarman",
            device_id=device,
            config_entry_id="upstream-solarman",
        )
        for entity, device in [
            ("number.m200", "device_m200"),
            ("number.m80", "device_m80"),
        ]
    }
    monkeypatch.setattr(
        module.er,
        "async_get",
        lambda _: SimpleNamespace(async_get=lambda entity: registry.get(entity)),
    )
    monkeypatch.setattr(module, "async_dispatcher_send", lambda *args: None)
    entry = SimpleNamespace(
        entry_id="real-solarman-pair",
        options={"enabled": False, "feed_in": False},
        data={
            "grid": "sensor.grid",
            "invert_grid": False,
            "batteries": [
                {
                    "device_id": "bk1",
                    "soc": "sensor.soc1",
                    "lower": "number.low1",
                    "upper": "number.high1",
                    "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
                },
                {
                    "device_id": "bk2",
                    "soc": "sensor.soc2",
                    "lower": "number.low2",
                    "upper": "number.high2",
                    "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
                },
            ],
        },
    )
    return EMSRuntime(hass, entry), hardware, lookup_file, states


@pytest.mark.asyncio
async def test_optimistic_zero_never_clears_shutdown_obligation(solarman_pair) -> None:
    runtime, hardware, _, states = solarman_pair
    await runtime.async_disable()
    assert float(states["number.m200"].state) == 0
    assert float(states["number.m80"].state) == 0
    await runtime._verify_shutdown(module.monotonic())
    assert runtime._shutdown_obligations == {"number.m200", "number.m80"}
    assert runtime.status != "disabled"
    # Hardware still reports 100%, despite the HA optimistic zero.
    assert hardware == {"device_m200": 10000, "device_m80": 10000}


@pytest.mark.asyncio
async def test_fresh_register_read_confirms_both_shutdowns(solarman_pair) -> None:
    runtime, hardware, _, _ = solarman_pair
    await runtime.async_disable()
    await runtime._verify_shutdown(module.monotonic())
    assert runtime._shutdown_obligations
    hardware.update(device_m200=0, device_m80=0)
    await runtime._verify_shutdown(module.monotonic())
    assert runtime._shutdown_obligations == set()
    assert runtime.status == "disabled"


@pytest.mark.asyncio
async def test_unsupported_solarman_profile_never_claims_verified_shutdown(solarman_pair) -> None:
    runtime, hardware, lookup_file, _ = solarman_pair
    lookup_file[0] = "other_vendor.yaml"
    hardware.update(device_m200=0, device_m80=0)
    await runtime.async_disable()
    await runtime._verify_shutdown(module.monotonic())
    assert runtime._shutdown_obligations == {"number.m200", "number.m80"}


@pytest.mark.asyncio
async def test_optimistic_positive_command_cannot_open_feedback_gate(solarman_pair) -> None:
    """A number's optimistic 1% cannot permit the next ramp while register 53 is old."""
    runtime, hardware, _, states = solarman_pair
    await runtime._apply({"number.m200": 20}, now=1000)
    assert states["number.m200"].state == "1.0"
    states["sensor.grid"].last_reported += timedelta(seconds=2)
    grid_time = states["sensor.grid"].last_reported
    assert not await runtime._feedback_ready(now=1010, grid_seq=grid_time)
    assert "number.m200" in runtime._pending
    hardware["device_m200"] = 100
    assert await runtime._feedback_ready(now=1010, grid_seq=grid_time)
    assert not runtime._pending
