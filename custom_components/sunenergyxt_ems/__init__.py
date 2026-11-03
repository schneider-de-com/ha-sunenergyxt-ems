"""SunEnergyXT EMS: orchestrate the existing Home Assistant integrations."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Start one household controller only after valid sensor data is available."""
    from homeassistant.const import Platform

    from .runtime import EMSRuntime

    runtime = EMSRuntime(hass, entry)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, (Platform.SENSOR, Platform.SWITCH))
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))
    await runtime.async_start()
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Use the persisted options to update switches without resetting the controller."""
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None:
        await runtime.async_change_options()


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Remove callbacks and sensor/switch platforms."""
    from homeassistant.const import Platform

    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if runtime is not None:
        await runtime.async_stop()
    result = await hass.config_entries.async_unload_platforms(
        entry, (Platform.SENSOR, Platform.SWITCH)
    )
    if result:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return result
