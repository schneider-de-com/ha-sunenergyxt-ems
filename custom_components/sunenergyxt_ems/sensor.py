"""Expose a simple EMS health/status sensor."""

from __future__ import annotations

from time import monotonic

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .inputs import fresh_power
from .runtime import EMSRuntime

PID_VALUES = (
    ("grid_raw", "raw_grid_w"),
    ("grid_effective", "effective_grid_w"),
    ("grid_error", "pid_error_w"),
    ("pid_proportional", "pid_p_w"),
    ("pid_integral", "pid_i_w"),
    ("pid_derivative", "pid_d_w"),
    ("controller_target", "target_w"),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    """Add an easy-to-read EMS status entity."""
    runtime: EMSRuntime = hass.data[DOMAIN][entry.entry_id]
    controller_device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "controller")},
        name="SunEnergyXT EMS",
        manufacturer="Community",
        model="Energy management",
    )
    entities: list[SensorEntity] = [EMSStatus(entry, runtime)]
    entities.extend(EMSControlValue(entry, runtime, key, field) for key, field in PID_VALUES)
    entities.extend(
        BatteryStatus(
            entry,
            runtime,
            record["device_id"],
            record.get("label", f"BK215 {index}"),
            controller_device.id,
        )
        for index, record in enumerate(entry.data["batteries"], start=1)
    )
    async_add_entities(entities)


class EMSStatus(SensorEntity):
    """Report whether the controller can currently regulate."""

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
    def extra_state_attributes(self) -> dict[str, object]:
        runtime = self._runtime
        state = runtime.controller.state
        return {
            "last_error": runtime.last_error,
            "control_mode": "adaptive_pid" if runtime.controller.adaptive_pid else "proportional",
            "requested_total_w": state.target_w,
            "pending_actuator_reads": sorted(runtime._pending),
            "pending_shutdown_reads": sorted(runtime._shutdown_pending),
            "deferred_shutdown": sorted(runtime._deferred_shutdown),
            "startup_hold_remaining_s": {
                key: runtime._start_hold.remaining(key, monotonic())
                for key in runtime._last_sent
                if runtime._start_hold.active(key, monotonic())
            },
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._runtime.signal, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


class EMSControlValue(SensorEntity):
    """One graphable control term; never pretend it is measured inverter AC."""

    _attr_should_poll = False
    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_native_unit_of_measurement = "W"

    def __init__(self, entry: ConfigEntry, runtime: EMSRuntime, key: str, field: str) -> None:
        self._runtime = runtime
        self._field = field
        self._attr_translation_key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, "controller")},
            name="SunEnergyXT EMS",
            manufacturer="Community",
            model="Energy management",
        )

    @property
    def native_value(self) -> float | None:
        if self._field == "raw_grid_w":
            return self._runtime._grid_raw_w
        if self._field == "effective_grid_w":
            return self._runtime._grid_filtered_w
        return getattr(self._runtime.controller.state, self._field, None)

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._runtime.signal, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()


class BatteryStatus(SensorEntity):
    """Expose one device with a status and diagnostics per configured BK215."""

    _attr_translation_key = "battery_status"
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(
        self,
        entry: ConfigEntry,
        runtime: EMSRuntime,
        device_id: str,
        label: str = "BK215",
        parent_device_id: str | None = None,
    ) -> None:
        self._runtime = runtime
        self._device_id = device_id
        self._attr_unique_id = f"{entry.entry_id}_battery_{device_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"battery_{device_id}")},
            via_device_id=parent_device_id,
            name=f"{label} (EMS)",
            manufacturer="Community",
            model="BK215 EMS controller",
        )

    def _battery(self):
        return next(
            (battery for battery in self._runtime._batteries() if battery.key == self._device_id),
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
        if self._device_id in state.ev3600_active:
            return "ev3600_charging"
        if self._device_id in state.ev3600_unknown:
            return "ev3600_unavailable"
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
            "dc_output_w": getattr(battery, "dc_output_w", None),
            "dc_sensor": getattr(self._runtime, "_dc_monitored", {}).get(self._device_id),
            "inverters_online": sum(inv.online for inv in battery.inverters),
            "inverters_total": len(battery.inverters),
            "pv_input_w": battery.pv_input_w,
            "full_charge_bypass": self._device_id in self._runtime.controller.state.full,
            "ev3600_charging": battery.ev3600_charging,
            "reported_limit_w": self._runtime._reported_outputs((battery,)),
            "measured_inverter_ac_w": {
                inverter.key: fresh_power(
                    self._runtime.hass,
                    self._runtime.entry.options.get("ac_power", {}).get(inverter.key),
                )
                for inverter in battery.inverters
                if self._runtime.entry.options.get("ac_power", {}).get(inverter.key)
            },
            "requested_power_w": sum(
                self._runtime.controller.state.outputs.get(inv.key, 0) for inv in battery.inverters
            ),
        }

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(
            async_dispatcher_connect(self.hass, self._runtime.signal, self._refresh)
        )

    @callback
    def _refresh(self) -> None:
        self.async_write_ha_state()
