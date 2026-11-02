"""Suggest Home Assistant entities using registry identities, never entity ID names."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .discovery import Candidate, find_batteries, suggest_grid, suggest_inverter_limit

# These suffixes are from the *registered unique IDs* of the SunEnergyXT
# integration. They are not entity_id or localized UI-name assumptions.
BK_REGISTRY_SUFFIXES = {
    "wifi_soc",
    "wifi_cnfg_disc_soc_min",
    "wifi_cnfg_chg_soc_max",
    "wifi_inputpower_total",
}


def _source_key(entry: er.RegistryEntry) -> str | None:
    """Identify the entity's purpose from its integration-owned unique ID."""
    unique_id = entry.unique_id or ""
    platform = entry.platform.lower()
    if platform == "sunenergyxt" and entry.config_entry_id:
        prefix = f"{entry.config_entry_id}_"
        if unique_id.startswith(prefix):
            suffix = unique_id[len(prefix):]
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
            entry for entry in entries
            if suggest_inverter_limit((entry,), device_id) == entry.entity_id
        ]
        if len(matches) == 1:
            entity = matches[0]
            found[entity.entity_id] = device_label(hass, device_id)
    return found
