"""User-visible HA startup pacing setting."""

from __future__ import annotations

from math import isfinite

from homeassistant.components.number import NumberEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .runtime import EMSRuntime

# (default, minimum, maximum, step, HA display unit)
# These bounds are identical to the configuration options flow.
NUMBER_SETTINGS: dict[str, tuple[float, float, float, float, str | None]] = {
    "start_hold_seconds": (30, 5, 60, 1, "s"),
}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Expose live-tunable controller parameters without editing YAML."""
    runtime: EMSRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(EMSNumberSetting(entry, runtime, key) for key in NUMBER_SETTINGS)


class EMSNumberSetting(NumberEntity):
    """Persist the configured inverter startup hold time."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, entry: ConfigEntry, runtime: EMSRuntime, key: str) -> None:
        self._entry = entry
        self._runtime = runtime
        self._key = key
        default, minimum, maximum, step, unit = NUMBER_SETTINGS[key]
        self._default = default
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        self._attr_native_min_value = minimum
        self._attr_native_max_value = maximum
        self._attr_native_step = step
        self._attr_native_unit_of_measurement = unit
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="SunEnergyXT EMS",
            manufacturer="Community",
            model="Energy management",
        )

    @property
    def native_value(self) -> float:
        """Read the persisted setting (also reflects options-flow updates)."""
        return float(self._entry.options.get(self._key, self._default))

    async def async_set_native_value(self, value: float) -> None:
        """Reject invalid values without modifying the active controller."""
        value = float(value)
        if not isfinite(value) or not self.native_min_value <= value <= self.native_max_value:
            raise HomeAssistantError(f"Invalid EMS setting for {self._key}")
        opts = {**self._entry.options, self._key: value}
        self._runtime.hass.config_entries.async_update_entry(self._entry, options=opts)
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Follow external options-flow and runtime changes."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._runtime.signal, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
