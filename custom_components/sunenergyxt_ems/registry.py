"""Suggest Home Assistant entities using registry identities, never entity ID names."""

from __future__ import annotations

from collections.abc import Mapping

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .discovery import (
    BK_SOURCE_DOMAINS,
    Candidate,
    find_batteries,
    suggest_grid,
    suggest_inverter_limit,
)
from .forecast import ForecastError

# These suffixes are from the *registered unique IDs* of the SunEnergyXT
# integration. They are not entity_id or localized UI-name assumptions.
BK_REGISTRY_SUFFIXES = {
    "wifi_soc",
    "wifi_cnfg_disc_soc_min",
    "wifi_cnfg_chg_soc_max",
    "wifi_inputpower_total",
    "wifi_outputpower_total",
}


def forecast_solar_entry(hass: HomeAssistant, mapping: Mapping[str, str]) -> str | None:
    """Identify the two provider-owned daily roles, independent of renamed IDs."""
    if not isinstance(mapping, Mapping):
        raise ForecastError("invalid_forecast_mapping")
    if not any(mapping.get(role) for role in ("today", "tomorrow")):
        return None
    registry = er.async_get(hass)
    records = [registry.async_get(mapping.get(role, "")) for role in ("today", "tomorrow")]
    if not any(item and item.platform == "forecast_solar" for item in records):
        return None
    owner = records[0].config_entry_id if records[0] else None
    for item, role in zip(records, ("today", "tomorrow"), strict=True):
        if (
            not owner
            or item is None
            or item.platform != "forecast_solar"
            or item.domain != "sensor"
            or item.config_entry_id != owner
            or item.disabled_by is not None
            or item.unique_id != f"{owner}_energy_production_{role}"
        ):
            raise ForecastError("invalid_forecast_solar_mapping")
        state = hass.states.get(item.entity_id)
        if state is None or state.attributes.get("unit_of_measurement") not in ("Wh", "kWh"):
            raise ForecastError("invalid_forecast_solar_unit")
    entry = hass.config_entries.async_get_entry(owner)
    if entry is None or entry.domain != "forecast_solar":
        raise ForecastError("invalid_forecast_solar_mapping")
    return owner


def suggested_forecasts(hass: HomeAssistant) -> dict[str, str]:
    """Suggest only one complete Solcast entry using provider-owned identities."""
    roles = {
        "total_kwh_forecast_today": "today",
        "total_kwh_forecast_tomorrow": "tomorrow",
        "lastupdated": "updated",
    }
    groups: dict[str, dict[str, str]] = {}
    for entity in getattr(er.async_get(hass), "entities", {}).values():
        role = roles.get(entity.unique_id)
        if (
            role
            and entity.platform == "solcast_solar"
            and entity.domain == "sensor"
            and entity.config_entry_id
            and entity.disabled_by is None
        ):
            groups.setdefault(entity.config_entry_id, {})[role] = entity.entity_id
    complete = [group for group in groups.values() if len(group) == 3]
    return complete[0] if len(complete) == 1 else {}


def _source_key(entry: er.RegistryEntry) -> str | None:
    """Identify the entity's purpose from its integration-owned unique ID."""
    unique_id = entry.unique_id or ""
    platform = entry.platform.lower()
    if platform in BK_SOURCE_DOMAINS and entry.config_entry_id:
        prefix = f"{entry.config_entry_id}_"
        if unique_id.startswith(prefix):
            suffix = unique_id[len(prefix) :]
            return suffix if suffix in BK_REGISTRY_SUFFIXES else None
        return None
    if (
        platform == "energy"
        and entry.domain == "sensor"
        and unique_id.startswith("energy_power_grid_combined_")
    ):
        return "net_power"
    if (
        platform == "solarman"
        and entry.domain == "number"
        and unique_id.lower().endswith("_active_power_regulation_number")
    ):
        return "active_power_regulation"
    # Other inverter integrations may publish a stable translation key.
    # Never infer power control from an arbitrary entity's display name.
    return getattr(entry, "translation_key", None)


def registry_candidates(hass: HomeAssistant) -> list[Candidate]:
    """Extract current entity IDs from registry entries, including renamed ones."""
    registry = er.async_get(hass)
    candidates: list[Candidate] = []
    for entry in registry.entities.values():
        key = _source_key(entry)
        if not key or entry.disabled_by is not None:
            continue
        state = hass.states.get(entry.entity_id)
        candidates.append(
            Candidate(
                entity_id=entry.entity_id,
                platform=entry.domain,
                device_id=entry.device_id,
                source_domain=entry.platform,
                translation_key=key,
                unit=state.attributes.get("unit_of_measurement") if state else None,
            )
        )
    return candidates


def battery_suggestions(hass: HomeAssistant) -> dict[str, dict[str, str]]:
    """Return BK215 systems grouped by device ID, not the entity ID prefix."""
    return find_batteries(registry_candidates(hass))


def registered_connection(hass: HomeAssistant, inverter_entity_id: str) -> str | None:
    """Find a Solarman connectivity binary sensor by stable device registry ID.

    The real M80's number stayed available through a 5 s Solarman outage on
    2026-11-03; its separate connectivity sensor recorded off -> on instead.
    Never depend on user-renamable entity IDs or localized display names.
    """
    registry = er.async_get(hass)
    inverter = registry.async_get(inverter_entity_id)
    if (
        inverter is None
        or inverter.platform.lower() != "solarman"
        or getattr(inverter, "domain", "number") != "number"
        or not inverter.device_id
    ):
        return None
    entries = getattr(registry, "entities", None)
    if entries is None:
        return None
    matches = [
        entry.entity_id
        for entry in entries.values()
        if entry.device_id == inverter.device_id
        and entry.platform.lower() == "solarman"
        and entry.domain == "binary_sensor"
        and getattr(entry, "original_device_class", None) == "connectivity"
        and entry.disabled_by is None
    ]
    return matches[0] if len(matches) == 1 else None


def registered_dc_output(hass: HomeAssistant, device_id: str) -> str | None:
    """Recover a DC sensor for a pre-beta entry using the exact BK215 device ID."""
    registry = er.async_get(hass)
    if getattr(registry, "entities", None) is None:
        return None
    return battery_suggestions(hass).get(device_id, {}).get("dc_output")


def device_label(hass: HomeAssistant, device_id: str) -> str:
    """Keep a readable name in the setup flow only."""
    device = dr.async_get(hass).async_get(device_id)
    if device:
        return device.name_by_user or device.name or device_id
    return device_id


def suggested_grid(hass: HomeAssistant) -> str | None:
    """Prefer the HA Energy Dashboard's combined grid power sensor."""
    return suggest_grid(registry_candidates(hass))


def device_rated_w(hass: HomeAssistant, entity_id: str) -> float | None:
    """Infer watts only when the actual control entity explicitly provides them."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    unit = state.attributes.get("unit_of_measurement")
    if unit != "W":
        # Percent controls don't imply any nominal AC rating.
        return None
    maximum = state.attributes.get("max")
    if isinstance(maximum, (float, int)) and 0 < maximum <= 5000:
        return float(maximum)
    return None


def inverter_device_suggestions(hass: HomeAssistant) -> dict[str, str]:
    """Display inverter *devices*, each backed by one verified writable limit.

    Returns a mapping of limit entity IDs to device labels. Unsupported or
    ambiguous devices can be selected manually on the fallback flow.
    """
    candidates = registry_candidates(hass)
    by_device: dict[str, list[Candidate]] = {}
    for candidate in candidates:
        if candidate.device_id and candidate.platform == "number":
            by_device.setdefault(candidate.device_id, []).append(candidate)
    found: dict[str, str] = {}
    for device_id, entries in by_device.items():
        matches = [
            entry
            for entry in entries
            if suggest_inverter_limit((entry,), device_id) == entry.entity_id
        ]
        if len(matches) == 1:
            entity = matches[0]
            found[entity.entity_id] = device_label(hass, device_id)
    return found
