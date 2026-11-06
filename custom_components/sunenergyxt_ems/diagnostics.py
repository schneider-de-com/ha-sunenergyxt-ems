"""Home Assistant diagnostics for the EMS configuration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_BATTERIES, CONF_GRID, CONF_GRID_INVERT, CONF_INVERTERS

_BATTERY_FIELDS = ("device_id", "label", "soc", "lower", "upper", "charging", "dc_output")
_INVERTER_FIELDS = ("entity_id", "rated_w")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Expose only the selected devices and measurement mappings, not arbitrary entry data."""
    batteries = []
    for battery in entry.data.get(CONF_BATTERIES, []):
        selected = {key: battery[key] for key in _BATTERY_FIELDS if key in battery}
        selected[CONF_INVERTERS] = [
            {key: inverter[key] for key in _INVERTER_FIELDS if key in inverter}
            for inverter in battery.get(CONF_INVERTERS, [])
        ]
        batteries.append(selected)
    return {
        "configuration": {
            CONF_GRID: entry.data.get(CONF_GRID),
            CONF_GRID_INVERT: entry.data.get(CONF_GRID_INVERT, False),
            CONF_BATTERIES: batteries,
        }
    }
