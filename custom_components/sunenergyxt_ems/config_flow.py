"""Short setup wizard for existing BK215 batteries and inverter integrations."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_BATTERIES,
    CONF_DEVICE,
    CONF_ENABLED,
    CONF_FEED_IN,
    CONF_GRID,
    CONF_GRID_INVERT,
    CONF_INVERTERS,
    CONF_LOWER,
    CONF_RATED_W,
    CONF_SOC,
    CONF_UPPER,
    DOMAIN,
)
from .registry import battery_suggestions, device_label, device_rated_w, suggested_grid

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

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Choose registered BK215 devices rather than individual entities."""
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        suggestions = battery_suggestions(self.hass)
        if not suggestions:
            return await self.async_step_battery_manual()
        if user_input is not None:
            selected = user_input.get(CONF_BATTERIES, [])
            if selected and all(device in suggestions for device in selected):
                self._batteries = [
                    {CONF_DEVICE: device_id, **suggestions[device_id], CONF_INVERTERS: []}
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
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_BATTERIES, default=list(suggestions)): selector.SelectSelector(
                        selector.SelectSelectorConfig(options=options, multiple=True)
                    ),
                    vol.Optional("correct_values", default=False):
                        selector.BooleanSelector(),
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
            return await self.async_step_inverters()
        fields: dict[Any, Any] = {}
        for index, battery in enumerate(self._batteries, start=1):
            fields[vol.Required(f"soc_{index}", default=battery.get(CONF_SOC))] = (
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor"))
            )
            lower_kwargs = {"default": battery[CONF_LOWER]} if battery.get(CONF_LOWER) else {}
            fields[vol.Required(f"lower_{index}", **lower_kwargs)] = (
                selector.EntitySelector(selector.EntitySelectorConfig(domain="number"))
            )
            if battery.get(CONF_UPPER):
                fields[vol.Optional(f"upper_{index}", default=battery[CONF_UPPER])] = (
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="number"))
                )
            else:
                fields[vol.Optional(f"upper_{index}")] = (
                    selector.EntitySelector(selector.EntitySelectorConfig(domain="number"))
                )
        return self.async_show_form(
            step_id="correct",
            data_schema=vol.Schema(fields),
        )

    async def async_step_battery_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Allow setup when the upstream BK215 device is not registered."""
        if user_input is not None:
            self._batteries = [{
                CONF_DEVICE: "manual", CONF_SOC: user_input[CONF_SOC],
                CONF_LOWER: user_input[CONF_LOWER],
                CONF_UPPER: user_input.get(CONF_UPPER),
                CONF_INVERTERS: [],
            }]
            return await self.async_step_inverters()
        return self.async_show_form(
            step_id="battery_manual",
            data_schema=vol.Schema({
                vol.Required(CONF_SOC): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_LOWER, default=10): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=90)
                ),
                vol.Optional(CONF_UPPER, default=90): vol.All(
                    vol.Coerce(float), vol.Range(min=70, max=100)
                ),
            }),
        )

    async def async_step_inverters(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select one or two independently writable inverter limits per BK215."""
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
                    if not registration or registration.domain != "number":
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
                        battery_inverters.append({
                            "entity_id": entity,
                            CONF_RATED_W: rating,
                        })
                        if rating is None:
                            self._pending_ratings.append((battery_index, inverter_index))
                    self._batteries[battery_index][CONF_INVERTERS] = battery_inverters
                return (
                    await self.async_step_ratings()
                    if self._pending_ratings
                    else await self.async_step_grid()
                )
        schema = vol.Schema({
            vol.Required(f"{CONF_INVERTER_PREFIX}{index}"): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="number", multiple=True)
            )
            for index, _ in enumerate(self._batteries, start=1)
        })
        return self.async_show_form(
            step_id="inverters",
            data_schema=schema,
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
            data_schema=vol.Schema({
                vol.Required(f"{CONF_RATING_PREFIX}{battery_index}_{inverter_index}"):
                    vol.All(vol.Coerce(float), vol.Range(min=1, max=3000))
                for battery_index, inverter_index in self._pending_ratings
            }),
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
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title="SunEnergyXT EMS",
                    data={
                        CONF_BATTERIES: self._batteries,
                        CONF_GRID: grid,
                        CONF_GRID_INVERT: user_input.get(CONF_GRID_INVERT, False),
                    },
                    options={CONF_ENABLED: True, CONF_FEED_IN: False},
                )
        suggestion = suggested_grid(self.hass)
        return self.async_show_form(
            step_id="grid",
            data_schema=vol.Schema({
                vol.Required(CONF_GRID, **(
                    {"default": suggestion} if suggestion else {}
                )): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(CONF_GRID_INVERT, default=False): selector.BooleanSelector(),
                vol.Required(CONF_ACK, default=False): selector.BooleanSelector(),
            }),
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
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({
                vol.Required(
                    CONF_ENABLED, default=self.config_entry.options.get(CONF_ENABLED, True)
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_FEED_IN, default=self.config_entry.options.get(CONF_FEED_IN, False)
                ): selector.BooleanSelector(),
            }),
        )
