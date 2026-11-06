"""Exercise the Home Assistant diagnostics download, including redaction."""

import json

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sunenergyxt_ems.const import DOMAIN


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_ha_diagnostics_exposes_selected_mapping_without_secrets(
    hass: HomeAssistant, hass_client
) -> None:
    """Only explicitly needed EMS fields reach the actual HA download API."""
    battery = {
        "device_id": "device-one",
        "soc": "sensor.battery",
        "lower": "number.minimum_soc",
        "upper": 95,
        "charging": "sensor.charging",
        "dc_output": "sensor.dc_power",
        "inverters": [{"entity_id": "number.m200", "rated_w": 2000, "token": "inv-secret"}],
        "password": "battery-secret",
    }
    second_battery = {
        "device_id": "device-two",
        "soc": "sensor.battery_two",
        "lower": 10,
        "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "invert_grid": True,
            "batteries": [battery, second_battery],
            "token": "secret",
        },
        options={"enabled": False, "password": "options-secret"},
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "diagnostics", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()
    response = await client.get(f"/api/diagnostics/config_entry/{entry.entry_id}")
    assert response.status == 200
    body = await response.json()
    assert body["data"]["configuration"] == {
        "grid": "sensor.grid",
        "invert_grid": True,
        "batteries": [
            {
                "device_id": "device-one",
                "soc": "sensor.battery",
                "lower": "number.minimum_soc",
                "upper": 95,
                "charging": "sensor.charging",
                "dc_output": "sensor.dc_power",
                "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
            },
            second_battery,
        ],
    }
    assert "secret" not in json.dumps(body["data"])
