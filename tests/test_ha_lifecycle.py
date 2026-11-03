"""Black-box Home Assistant lifecycle test for the installed integration.

Unlike the focused controller unit tests, this exercises HA's own config entry,
entity platform, device registry and entity registry. It must run as a required
CI job with pytest-homeassistant-custom-component; no skip-on-missing-plugin.
"""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sunenergyxt_ems.const import DOMAIN


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_setup_registers_real_integration_devices_and_controls(
    hass: HomeAssistant,
) -> None:
    """A loaded entry must create its devices and usable entities in real HA."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="SunEnergyXT EMS",
        data={
            "grid": "sensor.grid",
            "invert_grid": False,
            "batteries": [
                {
                    "device_id": "bk215-1",
                    "label": "BK215 1",
                    "soc": "sensor.soc",
                    "lower": 10,
                    "upper": 90,
                    "inverters": [
                        {"entity_id": "number.dummy_inverter", "rated_w": 800},
                    ],
                },
            ],
        },
        # Do not send any live commands in the integration lifecycle test.
        options={"enabled": False, "feed_in": False, "grid_target_w": 20},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 2, "Expected EMS hub and one BK215 child device"
    assert any(device.name == "SunEnergyXT EMS" for device in devices)
    assert any("BK215" in (device.name or "") for device in devices)

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    unique_ids = {entity.unique_id for entity in entities}
    assert unique_ids == {
        f"{entry.entry_id}_status",
        f"{entry.entry_id}_battery_bk215-1",
        f"{entry.entry_id}_enabled",
        f"{entry.entry_id}_feed_in",
    }
    assert all(hass.states.get(entity.entity_id) is not None for entity in entities)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
