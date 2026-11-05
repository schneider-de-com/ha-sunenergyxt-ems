"""Short setup wizard for existing BK215 batteries and inverter integrations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERIES,
    CONF_DC_TRIPPED,
    CONF_DEVICE,
    CONF_ENABLED,
    CONF_FEED_IN,
    CONF_FEED_IN_HYSTERESIS,
    CONF_FEED_IN_MODE,
    CONF_GRID,
    CONF_GRID_INVERT,
    CONF_INVERTERS,
    CONF_LOWER,
    CONF_RATED_W,
    CONF_SOC,
    CONF_UPPER,
    DEFAULT_GRID_TARGET_W,
    DOMAIN,
    FEED_IN_MODE_HOUSEHOLD,
    FEED_IN_MODE_PV,
)
from .registry import (
    battery_suggestions,
    device_label,
    device_rated_w,
    inverter_device_suggestions,
    suggested_grid,
)

CONF_ACK = "ack"
CONF_SELECTED = "selected"
CONF_INVERTER_PREFIX = "inverters_"
CONF_RATING_PREFIX = "rating_"


class SunEnergyXTEMSConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure one EMS for one shared household grid meter."""

    VERSION = 1

    def __init__(self) -> None:
        self._batteries: list[dict[str, Any]] = []
        self._grid: str | None = None
        self._invert_grid = False
        self._pending_ratings: list[tuple[int, int]] = []
        self._manual_inverters = False
        self._reconfigure = False
        self._previous: dict[str, Any] = {}

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Update batteries, inverters and grid meter without deleting the integration."""
        self._reconfigure = True
        self._previous = dict(self._get_reconfigure_entry().data)
        return await self.async_step_user(user_input)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose registered BK215 devices rather than individual entities."""
        if not self._reconfigure and self._async_current_entries():
            return self.async_abort(reason="already_configured")
        suggestions = battery_suggestions(self.hass)
        if self._reconfigure:
            for battery in self._previous.get(CONF_BATTERIES, []):
                suggestions.setdefault(
                    battery[CONF_DEVICE],
                    {
                        key: value
                        for key, value in battery.items()
                        if key in (CONF_SOC, CONF_LOWER, CONF_UPPER, "charging", "dc_output")
                    },
                )
        if not suggestions:
            return await self.async_step_battery_manual()
        if user_input is not None:
            selected = user_input.get(CONF_BATTERIES, [])
            if selected and all(device in suggestions for device in selected):
                self._batteries = [
                    {
                        CONF_DEVICE: device_id,
                        "label": device_label(self.hass, device_id),
                        **suggestions[device_id],
                        CONF_INVERTERS: [],
                    }
                    for device_id in selected
                ]
                if user_input.get("correct_values") or any(
                    CONF_LOWER not in battery for battery in self._batteries
                ):
                    return await self.async_step_correct()
                return await self.async_step_inverters()
            errors = {"base": "no_battery"}
        else:
            errors = {}
        options = [
            {"value": device_id, "label": device_label(self.hass, device_id)}
            for device_id in suggestions
        ]
        return self.async_show_form(
            step_id="reconfigure" if self._reconfigure else "user",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_BATTERIES,
                        default=[b[CONF_DEVICE] for b in self._previous.get(CONF_BATTERIES, [])]
                        if self._reconfigure
                        else list(suggestions),
                    ): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, multiple=True)
                    ),
                    vol.Optional("correct_values", default=False): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )

    async def async_step_correct(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Offer a short optional correction page for detected device entities."""
        if user_input is not None:
            for index, battery in enumerate(self._batteries, start=1):
                battery[CONF_SOC] = user_input[f"soc_{index}"]
                battery[CONF_LOWER] = user_input[f"lower_{index}"]
                upper = user_input.get(f"upper_{index}")
                if upper:
                    battery[CONF_UPPER] = upper
                dc_output = user_input.get(f"dc_output_{index}")
                if dc_output:
                    battery["dc_output"] = dc_output
            return await self.async_step_inverters()
        fields: dict[Any, Any] = {}
        for index, battery in enumerate(self._batteries, start=1):
            fields[vol.Required(f"soc_{index}", default=battery.get(CONF_SOC))] = (
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
            )
            lower_kwargs = {"default": battery[CONF_LOWER]} if battery.get(CONF_LOWER) else {}
            if battery.get(CONF_LOWER):
                fields[vol.Required(f"lower_{index}", **lower_kwargs)] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="number")
                )
            else:
                fields[vol.Required(f"lower_{index}", default=10)] = vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=90)
                )
            if battery.get(CONF_UPPER):
                fields[vol.Required(f"upper_{index}", default=battery[CONF_UPPER])] = (
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="number"))
                )
            else:
                fields[vol.Optional(f"upper_{index}")] = vol.All(
                    vol.Coerce(float), vol.Range(min=70, max=100)
                )
            fields[
                vol.Optional(
                    f"dc_output_{index}",
                    **({"default": battery["dc_output"]} if battery.get("dc_output") else {}),
                )
            ] = selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
        return self.async_show_form(
            step_id="correct",
            data_schema=vol.Schema(fields),
        )

    async def async_step_battery_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow setup when the upstream BK215 device is not registered."""
        if user_input is not None:
            self._batteries = [
                {
                    CONF_DEVICE: "manual",
                    CONF_SOC: user_input[CONF_SOC],
                    CONF_LOWER: user_input[CONF_LOWER],
                    CONF_UPPER: user_input.get(CONF_UPPER),
                    CONF_INVERTERS: [],
                }
            ]
            return await self.async_step_inverters()
        return self.async_show_form(
            step_id="battery_manual",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_SOC): selector.EntitySelector(
                        selector.EntitySelectorConfig(domain="sensor")
                    ),
                    vol.Required(CONF_LOWER, default=10): vol.All(
                        vol.Coerce(float), vol.Range(min=0, max=90)
                    ),
                    vol.Optional(CONF_UPPER, default=90): vol.All(
                        vol.Coerce(float), vol.Range(min=70, max=100)
                    ),
                }
            ),
        )

    async def async_step_inverters_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Use a manual entity selector only for unknown inverter brands."""
        self._manual_inverters = True
        return await self.async_step_inverters(user_input)

    async def async_step_inverters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one or two independently writable inverter limits per BK215."""
        suggestions = inverter_device_suggestions(self.hass)
        if not suggestions and not self._manual_inverters:
            return await self.async_step_inverters_manual()
        if user_input is not None and user_input.get("manual_inverters"):
            return await self.async_step_inverters_manual()
        errors: dict[str, str] = {}
        if user_input is not None:
            seen: set[str] = set()
            registry = er.async_get(self.hass)
            pending: list[list[str]] = []
            for index, _ in enumerate(self._batteries, start=1):
                entities = user_input.get(f"{CONF_INVERTER_PREFIX}{index}", [])
                if not isinstance(entities, list) or not 1 <= len(entities) <= 2:
                    errors["base"] = "invalid_inverter"
                    break
                if any(entity in seen for entity in entities):
                    errors["base"] = "duplicate_inverter"
                    break
                for entity in entities:
                    registration = registry.async_get(entity)
                    state = self.hass.states.get(entity)
                    if (
                        not registration
                        or registration.domain != "number"
                        or state is None
                        or state.attributes.get("unit_of_measurement") not in ("W", "%")
                    ):
                        errors["base"] = "invalid_inverter"
                        break
                if errors:
                    break
                seen.update(entities)
                pending.append(entities)
            if not errors:
                self._pending_ratings = []
                for battery_index, entities in enumerate(pending):
                    battery_inverters = []
                    for inverter_index, entity in enumerate(entities):
                        rating = device_rated_w(self.hass, entity)
                        if rating is None and self._reconfigure:
                            rating = next(
                                (
                                    previous[CONF_RATED_W]
                                    for stored in self._previous.get(CONF_BATTERIES, [])
                                    for previous in stored.get(CONF_INVERTERS, [])
                                    if previous["entity_id"] == entity
                                ),
                                None,
                            )
                        battery_inverters.append(
                            {
                                "entity_id": entity,
                                CONF_RATED_W: rating,
                            }
                        )
                        if rating is None:
                            self._pending_ratings.append((battery_index, inverter_index))
                    self._batteries[battery_index][CONF_INVERTERS] = battery_inverters
                return (
                    await self.async_step_ratings()
                    if self._pending_ratings
                    else await self.async_step_grid()
                )
        fields: dict[Any, Any] = {}
        for index, battery in enumerate(self._batteries, start=1):
            old = next(
                (
                    item
                    for item in self._previous.get(CONF_BATTERIES, [])
                    if item[CONF_DEVICE] == battery[CONF_DEVICE]
                ),
                {},
            )
            defaults = [inv["entity_id"] for inv in old.get(CONF_INVERTERS, [])]
            marker = vol.Required(
                f"{CONF_INVERTER_PREFIX}{index}",
                **({"default": defaults} if defaults else {}),
            )
            if self._manual_inverters:
                fields[marker] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="number", multiple=True)
                )
            else:
                options = [
                    {"value": entity, "label": label} for entity, label in suggestions.items()
                ]
                fields[marker] = selector.SelectSelector(
                    selector.SelectSelectorConfig(options=options, multiple=True)
                )
        if not self._manual_inverters:
            fields[vol.Optional("manual_inverters", default=False)] = selector.BooleanSelector()
        return self.async_show_form(
            step_id="inverters_manual" if self._manual_inverters else "inverters",
            data_schema=vol.Schema(fields),
            errors=errors,
        )

    async def async_step_ratings(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask inverter watt ratings only when they cannot be inferred."""
        if user_input is not None:
            for battery_index, inverter_index in self._pending_ratings:
                key = f"{CONF_RATING_PREFIX}{battery_index}_{inverter_index}"
                self._batteries[battery_index][CONF_INVERTERS][inverter_index][CONF_RATED_W] = (
                    float(user_input[key])
                )
            return await self.async_step_grid()
        return self.async_show_form(
            step_id="ratings",
            data_schema=vol.Schema(
                {
                    vol.Required(f"{CONF_RATING_PREFIX}{battery_index}_{inverter_index}"): vol.All(
                        vol.Coerce(float), vol.Range(min=1, max=3000)
                    )
                    for battery_index, inverter_index in self._pending_ratings
                }
            ),
        )

    async def async_step_grid(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Select a signed real-time grid meter and confirm single-writer control."""
        errors: dict[str, str] = {}
        if user_input is not None:
            grid = user_input[CONF_GRID]
            state = self.hass.states.get(grid)
            if not state or state.attributes.get("unit_of_measurement") not in ("W", "kW"):
                errors["base"] = "invalid_setup"
            elif not user_input.get(CONF_ACK, False):
                errors["base"] = "other_controller"
            else:
                await self.async_set_unique_id("household")
                data = {
                    CONF_BATTERIES: self._batteries,
                    CONF_GRID: grid,
                    CONF_GRID_INVERT: user_input.get(CONF_GRID_INVERT, False),
                }
                if self._reconfigure:
                    self._abort_if_unique_id_mismatch()
                    entry = self._get_reconfigure_entry()
                    runtime = getattr(self.hass, "data", {}).get(DOMAIN, {}).get(entry.entry_id)
                    if runtime is not None:
                        # Reconfiguration is an explicit handover: do not leave
                        # a removed inverter running at an old EMS limit.
                        if getattr(getattr(runtime, "controller", None), "enabled", True):
                            await runtime.async_disable()
                            if hasattr(self.hass, "config_entries"):
                                # Do not resume the old controller if the user abandons reconfiguration.
                                self.hass.config_entries.async_update_entry(
                                    entry, options={**entry.options, CONF_ENABLED: False}
                                )
                        if (
                            runtime.last_error
                            or getattr(runtime, "_shutdown_pending", {})
                            or getattr(runtime, "_shutdown_obligations", set())
                        ):
                            errors["base"] = "inverter_stop_failed"
                        else:
                            return self.async_update_reload_and_abort(entry, data_updates=data)
                    else:
                        return self.async_update_reload_and_abort(entry, data_updates=data)
                else:
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title="SunEnergyXT EMS",
                        data=data,
                        options={
                            CONF_ENABLED: True,
                            CONF_FEED_IN: False,
                            CONF_FEED_IN_MODE: FEED_IN_MODE_HOUSEHOLD,
                            CONF_FEED_IN_HYSTERESIS: 2.0,
                            "grid_target_w": DEFAULT_GRID_TARGET_W,
                        },
                    )
        suggestion = self._previous.get(CONF_GRID) or suggested_grid(self.hass)
        return self.async_show_form(
            step_id="grid",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_GRID, **({"default": suggestion} if suggestion else {})
                    ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                    vol.Optional(
                        CONF_GRID_INVERT, default=self._previous.get(CONF_GRID_INVERT, False)
                    ): selector.BooleanSelector(),
                    vol.Required(CONF_ACK, default=False): selector.BooleanSelector(),
                }
            ),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> EMSEntryOptionsFlow:
        """Allow changing active mode and optional feed-in after setup."""
        return EMSEntryOptionsFlow()


class EMSEntryOptionsFlow(config_entries.OptionsFlow):
    """Simple toggles without duplicate sensor configuration."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Keep operating settings editable without reinstalling the integration."""
        existing = list(getattr(self.config_entry, "data", {}).get(CONF_BATTERIES, []))
        errors: dict[str, str] = {}
        if user_input is not None:
            requested = dict(user_input)
            # Stage changes to existing upper SoC mappings. HA's Options Flow
            # must not mutate config-entry data before all validation succeeds.
            new_batteries = [dict(battery) for battery in existing]
            for index, battery in enumerate(existing, start=1):
                old = battery.get(CONF_UPPER)
                source = requested.pop(f"upper_source_{index}", None)
                percent = requested.pop(f"upper_pct_{index}", requested.pop(f"upper_{index}", None))
                changed_source = bool(source) and source != old
                changed_percent = percent is not None and (
                    not isinstance(old, (int, float)) or float(percent) != float(old)
                )
                if changed_source:
                    new_upper = source
                elif changed_percent:
                    new_upper = float(percent)
                else:
                    new_upper = old
                if new_upper is not None:
                    new_batteries[index - 1][CONF_UPPER] = new_upper
            reset_trip = bool(requested.pop("reset_dc_trip", False))
            pv_map = dict(self.config_entry.options.get("pv_input", {}))
            ev_map = dict(self.config_entry.options.get("ev3600", {}))
            ac_map = dict(self.config_entry.options.get("ac_power", {}))
            capacities = dict(self.config_entry.options.get("capacity_wh", {}))
            for index, battery in enumerate(existing, start=1):
                device = battery[CONF_DEVICE]
                capacity = requested.pop(f"capacity_wh_{index}", capacities.get(device))
                if capacity is not None:
                    capacities[device] = capacity
                pv = requested.pop(f"pv_input_{index}", pv_map.get(device))
                mode = requested.pop(f"ev_mode_{index}", ev_map.get(device, {}).get("mode"))
                power = requested.pop(f"ev_power_{index}", ev_map.get(device, {}).get("power"))
                if pv:
                    pv_map[device] = pv
                else:
                    pv_map.pop(device, None)
                if bool(mode) != bool(power):
                    errors["base"] = "incomplete_ev3600"
                elif mode:
                    ev_map[device] = {"mode": mode, "power": power}
                else:
                    ev_map.pop(device, None)
            for i, battery in enumerate(existing, 1):
                for j, inverter in enumerate(battery.get(CONF_INVERTERS, ()), 1):
                    key = inverter["entity_id"]
                    source = requested.pop(f"ac_{i}_{j}", ac_map.get(key))
                    if source:
                        ac_map[key] = source
                    else:
                        ac_map.pop(key, None)
            if reset_trip:
                if requested.get(CONF_ENABLED, True):
                    errors["base"] = "disable_before_trip_reset"
                else:
                    tripped = set(self.config_entry.options.get(CONF_DC_TRIPPED, ()))
                    records = self.config_entry.data.get(CONF_BATTERIES, ())
                    matching = [
                        battery for battery in records if battery.get(CONF_DEVICE) in tripped
                    ]
                    safe_to_reset = bool(tripped) and len(matching) == len(tripped)
                    for battery in matching:
                        source = battery.get("dc_output") or battery_suggestions(self.hass).get(
                            battery[CONF_DEVICE], {}
                        ).get("dc_output")
                        state = self.hass.states.get(source) if source else None
                        report = getattr(state, "last_reported", None) or getattr(
                            state, "last_updated", None
                        )
                        try:
                            watts = float(state.state) if state else float("nan")
                        except TypeError, ValueError:
                            watts = float("nan")
                        if (
                            state is None
                            or state.attributes.get("unit_of_measurement") != "W"
                            or not isinstance(report, datetime)
                            or report.tzinfo is None
                            or not 0 <= (datetime.now(UTC) - report).total_seconds() <= 90
                            or not 0 <= watts <= 100
                        ):
                            safe_to_reset = False
                    if not safe_to_reset:
                        errors["base"] = "trip_reset_unconfirmed"
            if (
                requested.get(CONF_FEED_IN)
                and requested.get(
                    CONF_FEED_IN_MODE,
                    self.config_entry.options.get(CONF_FEED_IN_MODE, FEED_IN_MODE_PV),
                )
                == FEED_IN_MODE_PV
                and any(battery.get(CONF_UPPER) is None for battery in new_batteries)
            ):
                errors["base"] = "missing_upper"
            if float(requested.get("pid_kp_max", 0.9)) < float(requested.get("pid_kp_min", 0.5)):
                errors["base"] = "invalid_pid_gains"
            if not errors:
                if requested.get(CONF_ENABLED) is False and self.config_entry.options.get(
                    CONF_ENABLED, True
                ):
                    runtimes = getattr(getattr(self, "hass", None), "data", {}).get(DOMAIN, {})
                    runtime = runtimes.get(getattr(self.config_entry, "entry_id", None))
                    if runtime is not None:
                        await runtime.async_disable()
                if new_batteries != existing:
                    updated = {**self.config_entry.data, CONF_BATTERIES: new_batteries}
                    self.hass.config_entries.async_update_entry(self.config_entry, data=updated)
                settings = {
                    key: value
                    for key, value in requested.items()
                    if key
                    in (
                        CONF_ENABLED,
                        CONF_FEED_IN,
                        CONF_FEED_IN_MODE,
                        CONF_FEED_IN_HYSTERESIS,
                        "grid_target_w",
                        "adaptive_pid",
                        "pid_kp_min",
                        "pid_kp_max",
                        "pid_kp_export",
                        "pid_ki",
                        "pid_kd",
                        "start_hold_seconds",
                    )
                }
                settings["pv_input"] = pv_map
                settings["ev3600"] = ev_map
                settings["ac_power"] = ac_map
                settings["capacity_wh"] = capacities
                settings[CONF_DC_TRIPPED] = (
                    [] if reset_trip else list(self.config_entry.options.get(CONF_DC_TRIPPED, ()))
                )
                return self.async_create_entry(title="", data=settings)
        fields: dict[Any, Any] = {
            vol.Required(
                CONF_ENABLED,
                default=self.config_entry.options.get(CONF_ENABLED, True),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_FEED_IN,
                default=self.config_entry.options.get(CONF_FEED_IN, False),
            ): selector.BooleanSelector(),
            vol.Required(
                CONF_FEED_IN_MODE,
                default=self.config_entry.options.get(CONF_FEED_IN_MODE, FEED_IN_MODE_PV),
            ): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=[
                        {
                            "value": FEED_IN_MODE_HOUSEHOLD,
                            "label": "Household load / Haushaltslast",
                        },
                        {
                            "value": FEED_IN_MODE_PV,
                            "label": "Full charge then max inverter / PV-Erzeugung",
                        },
                    ]
                )
            ),
            vol.Required(
                CONF_FEED_IN_HYSTERESIS,
                default=self.config_entry.options.get(CONF_FEED_IN_HYSTERESIS, 2.0),
            ): vol.All(vol.Coerce(float), vol.Range(min=1, max=10)),
            vol.Required(
                "grid_target_w",
                default=self.config_entry.options.get("grid_target_w", DEFAULT_GRID_TARGET_W),
            ): vol.All(vol.Coerce(float), vol.Range(min=0, max=200)),
        }
        fields[
            vol.Optional(
                "adaptive_pid", default=self.config_entry.options.get("adaptive_pid", False)
            )
        ] = selector.BooleanSelector()
        for key, default, minimum, maximum in (
            ("start_hold_seconds", 30, 5, 60),
            ("pid_kp_min", 0.5, 0, 2),
            ("pid_kp_max", 0.9, 0, 2),
            ("pid_kp_export", 1.0, 0, 2),
            ("pid_ki", 0.02, 0, 0.5),
            ("pid_kd", 0.12, 0, 1),
        ):
            fields[vol.Optional(key, default=self.config_entry.options.get(key, default))] = (
                vol.All(vol.Coerce(float), vol.Range(min=minimum, max=maximum))
            )
        for index, battery in enumerate(existing, start=1):
            device = battery[CONF_DEVICE]
            capacity = self.config_entry.options.get("capacity_wh", {}).get(device)
            fields[
                vol.Optional(f"capacity_wh_{index}", **({"default": capacity} if capacity else {}))
            ] = vol.All(vol.Coerce(float), vol.Range(min=1))
            pv = self.config_entry.options.get("pv_input", {}).get(device)
            ev = self.config_entry.options.get("ev3600", {}).get(device, {})
            for prefix, source, domain in (
                ("ev_mode", ev.get("mode"), "switch"),
                ("ev_power", ev.get("power"), "sensor"),
            ):
                key = vol.Optional(
                    f"{prefix}_{index}",
                    **({"default": source} if source else {}),
                )
                fields[key] = selector.EntitySelector(selector.EntitySelectorConfig(domain=domain))
            fields[vol.Optional(f"pv_input_{index}", **({"default": pv} if pv else {}))] = (
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
            )
        for i, battery in enumerate(existing, 1):
            for j, inverter in enumerate(battery.get(CONF_INVERTERS, ()), 1):
                source = self.config_entry.options.get("ac_power", {}).get(inverter["entity_id"])
                key = vol.Optional(f"ac_{i}_{j}", **({"default": source} if source else {}))
                fields[key] = selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                )
        if self.config_entry.options.get(CONF_DC_TRIPPED):
            fields[vol.Optional("reset_dc_trip", default=False)] = selector.BooleanSelector()
        # Existing entries may already have an upper SoC source; always let
        # the user inspect or correct it. Changing this EMS mapping never
        # changes the BK215 hardware charging ceiling.
        for index, battery in enumerate(existing, start=1):
            upper = battery.get(CONF_UPPER)
            source = upper if isinstance(upper, str) else None
            fields[
                vol.Optional(f"upper_source_{index}", **({"default": source} if source else {}))
            ] = selector.EntitySelector(
                selector.EntitySelectorConfig(domain=["number", "sensor", "input_number"])
            )
            fields[
                vol.Optional(
                    f"upper_pct_{index}",
                    **({"default": upper} if isinstance(upper, (float, int)) else {}),
                )
            ] = vol.All(vol.Coerce(float), vol.Range(min=70, max=100))
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(fields),
            errors=errors,
        )
