"""User-facing EMS and feed-in switches."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.device_registry import DeviceInfo

from .const import CONF_ENABLED, CONF_FEED_IN, DOMAIN
from .runtime import EMSRuntime


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Create user toggles for the single EMS configuration."""
    runtime: EMSRuntime = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([
        EMSToggle(entry, runtime, CONF_ENABLED, "EMS control"),
        EMSToggle(entry, runtime, CONF_FEED_IN, "Feed-in after full charge"),
    ])


class EMSToggle(SwitchEntity):
    """Persist the user-selected control state in HA config entry options."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, entry: ConfigEntry, runtime: EMSRuntime, key: str, name: str
    ) -> None:
        self._entry = entry
        self._runtime = runtime
        self._key = key
        self._attr_name = None
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="SunEnergyXT EMS",
            manufacturer="Community",
            model="Energy management",
        )

    @property
    def is_on(self) -> bool:
        """Return the persisted setting."""
        return bool(self._entry.options.get(self._key, self._key == CONF_ENABLED))

    async def async_turn_on(self, **kwargs: object) -> None:
        """Enable one control option."""
        self._runtime.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, self._key: True}
        )
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: object) -> None:
        """Disable one control option; runtime handles safe one-shot lowering."""
        self._runtime.hass.config_entries.async_update_entry(
            self._entry, options={**self._entry.options, self._key: False}
        )
        self.async_write_ha_state()

    async def async_added_to_hass(self) -> None:
        """Listen for state changes from the scheduler and options."""
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._runtime.signal, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
