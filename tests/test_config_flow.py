"""Home Assistant setup tests: device discovery, wizard steps, and input checks."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import config_flow as flow_module  # noqa: E402
from custom_components.sunenergyxt_ems.config_flow import (  # noqa: E402
    SunEnergyXTEMSConfigFlow,
)


@pytest.fixture
def flow(monkeypatch):
    obj = SunEnergyXTEMSConfigFlow()
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity: SimpleNamespace(attributes={"unit_of_measurement": "W"})
        )
    )
    obj.hass = hass
    monkeypatch.setattr(obj, "_async_current_entries", lambda: [])
    monkeypatch.setattr(
        flow_module,
        "battery_suggestions",
        lambda _: {"battery-1": {
            "soc": "sensor.soc",
            "lower": "number.lower",
            "upper": "number.upper",
        }},
    )
    monkeypatch.setattr(flow_module, "device_label", lambda hass, device: "BK215")
    monkeypatch.setattr(
        flow_module.er, "async_get",
        lambda hass: SimpleNamespace(
            async_get=lambda entity: SimpleNamespace(domain="number")
        ),
    )
    monkeypatch.setattr(flow_module, "device_rated_w", lambda hass, entity: 800.0)
    monkeypatch.setattr(flow_module, "suggested_grid", lambda hass: "sensor.grid")
    return obj


@pytest.mark.asyncio
async def test_positive_setup_uses_device_instead_of_three_manual_entities(flow, monkeypatch):
    result = await flow.async_step_user()
    assert result["step_id"] == "user"

    result = await flow.async_step_user({"batteries": ["battery-1"]})
    assert result["step_id"] == "inverters"
    assert flow._batteries[0]["soc"] == "sensor.soc"
    assert flow._batteries[0]["lower"] == "number.lower"

    result = await flow.async_step_inverters({
        "inverters_1": ["number.inverter"]
    })
    assert result["step_id"] == "grid"

    monkeypatch.setattr(flow, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(flow, "_abort_if_unique_id_configured", lambda: None)
    result = await flow.async_step_grid({
        "grid": "sensor.grid",
        "ack": True,
        "invert_grid": False,
    })
    assert result["type"] == "create_entry"
    assert result["options"]["enabled"] is True
    assert result["options"]["feed_in"] is False


@pytest.mark.asyncio
async def test_negative_duplicate_inverter_is_rejected(flow):
    flow._batteries = [
        {"device_id": "a", "inverters": []},
        {"device_id": "b", "inverters": []},
    ]
    result = await flow.async_step_inverters({
        "inverters_1": ["number.same"],
        "inverters_2": ["number.same"],
    })
    assert result["errors"]["base"] == "duplicate_inverter"


@pytest.mark.asyncio
async def test_negative_setup_requires_disabling_other_controllers(flow):
    flow._batteries = [{"device_id": "a", "inverters": []}]
    result = await flow.async_step_grid({
        "grid": "sensor.grid", "ack": False
    })
    assert result["errors"]["base"] == "other_controller"



@pytest.mark.asyncio
async def test_positive_explicit_correction_step_preserves_automatic_suggestions(flow):
    result = await flow.async_step_user({
        "batteries": ["battery-1"],
        "correct_values": True,
    })
    assert result["step_id"] == "correct"
    result = await flow.async_step_correct({
        "soc_1": "sensor.updated_soc",
        "lower_1": "number.lower",
        "upper_1": "number.upper",
    })
    assert result["step_id"] == "inverters"
    assert flow._batteries[0]["soc"] == "sensor.updated_soc"


@pytest.mark.asyncio
async def test_negative_missing_upper_limit_requires_correction(flow, monkeypatch):
    monkeypatch.setattr(
        flow_module, "battery_suggestions",
        lambda _: {"battery-1": {
            "soc": "sensor.soc",
            "lower": "number.lower",
        }},
    )
    result = await flow.async_step_user({"batteries": ["battery-1"]})
    assert result["step_id"] == "correct"


@pytest.mark.asyncio
async def test_positive_manual_battery_fallback_when_upstream_not_found(flow, monkeypatch):
    monkeypatch.setattr(flow_module, "battery_suggestions", lambda _: {})
    result = await flow.async_step_user()
    assert result["step_id"] == "battery_manual"
    result = await flow.async_step_battery_manual({
        "soc": "sensor.manual_soc",
        "lower": 10,
        "upper": 90,
    })
    assert result["step_id"] == "inverters"
    assert flow._batteries[0]["lower"] == 10
