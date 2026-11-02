"""Expose a simple EMS health/status sensor."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .runtime import EMSRuntime


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add an easy-to-read EMS status entity."""
    runtime: EMSRuntime = hass.data[DOMAIN][entry.entry_id]
    entities: list[SensorEntity] = [EMSStatus(entry, runtime)]
    entities.extend(
        BatteryStatus(
            entry, runtime, record["device_id"],
            record.get("label", f"BK215 {index}"),
        )
        for index, record in enumerate(entry.data["batteries"], start=1)
    )
    async_add_entities(entities)


class EMSStatus(SensorEntity):
    """Report whether the controller can currently regulate."""

    _attr_name = None
    _attr_translation_key = "status"
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: ConfigEntry, runtime: EMSRuntime) -> None:
        self._runtime = runtime
        self._attr_unique_id = f"{entry.entry_id}_status"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="SunEnergyXT EMS",
            manufacturer="Community",
            model="Energy management",
        )

    @property
    def native_value(self) -> str:
        return self._runtime.status

    @property
    def extra_state_attributes(self) -> dict[str, str] | None:
        if self._runtime.last_error:
            return {"last_error": self._runtime.last_error}
        return None

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._runtime.signal, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


class BatteryStatus(SensorEntity):
    """Expose one device with a status and diagnostics per configured BK215."""

    _attr_name = None
    _attr_translation_key = "battery_status"
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self, entry: ConfigEntry, runtime: EMSRuntime,
        device_id: str, label: str = "BK215",
    ) -> None:
        self._runtime = runtime
        self._device_id = device_id
        self._attr_unique_id = f"{entry.entry_id}_battery_{device_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"battery_{device_id}")},
            via_device=(DOMAIN, "controller"),
            name=f"{label} (EMS)",
            manufacturer="Community",
            model="BK215 EMS controller",
        )

    def _battery(self):
        return next(
            (battery for battery in self._runtime._batteries()
             if battery.key == self._device_id),
            None,
        )

    @property
    def native_value(self) -> str:
        """Describe this battery's role, independently of other batteries."""
        state = self._runtime.controller.state
        if not self._runtime.controller.enabled:
            return "disabled"
        if self._device_id in state.blocked:
            return "blocked"
        battery = self._battery()
        if battery is None or not any(inverter.online for inverter in battery.inverters):
            return "unavailable"
        if state.main == self._device_id:
            return "main"
        if state.support == self._device_id:
            return "support"
        return "standby"

    @property
    def extra_state_attributes(self) -> dict[str, object]:
        battery = self._battery()
        if battery is None:
            return {}
        return {
            "soc_pct": battery.soc,
            "lower_limit_pct": battery.lower_soc,
            "upper_limit_pct": battery.upper_soc,
            "inverters_online": sum(inv.online for inv in battery.inverters),
            "inverters_total": len(battery.inverters),
            "requested_power_w": sum(
                self._runtime.controller.state.outputs.get(inv.key, 0)
                for inv in battery.inverters
            ),
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._runtime.signal, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
