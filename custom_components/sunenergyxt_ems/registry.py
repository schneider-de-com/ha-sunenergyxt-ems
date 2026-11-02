"""Map existing Home Assistant device and entity registries to EMS suggestions."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

from .discovery import Candidate, find_batteries, suggest_grid

KEYS = (
    "wifi_soc", "wifi_cnfg_disc_soc_min", "wifi_cnfg_chg_soc_max",
    "wifi_inputpower_total",
)


def registry_candidates(hass: HomeAssistant) -> list[Candidate]:
    """Identify entities by upstream platform and stable unique IDs."""
    registry = er.async_get(hass)
    candidates = []
    for entry in registry.entities.values():
        state = hass.states.get(entry.entity_id)
        unique_id = entry.unique_id or ""
        key = next((item for item in KEYS if unique_id.endswith("_" + item)), None)
        if key is None:
            key = getattr(entry, "translation_key", None)
        if key is None:
            key = unique_id.rsplit("_", 1)[-1]
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
    """Return BK215 systems grouped by registered device ID."""
    return find_batteries(registry_candidates(hass))


def device_label(hass: HomeAssistant, device_id: str) -> str:
    """Use friendly device names while storing stable device IDs."""
    device = dr.async_get(hass).async_get(device_id)
    if device:
        return device.name_by_user or device.name or device_id
    return device_id


def suggested_grid(hass: HomeAssistant) -> str | None:
    """Find an instantaneous grid sensor; users can always override it."""
    return suggest_grid(registry_candidates(hass))


def device_rated_w(hass: HomeAssistant, entity_id: str) -> float | None:
    """Infer inverter rating only from explicit watt limits or known models."""
    state = hass.states.get(entity_id)
    if state is None:
        return None
    unit = state.attributes.get("unit_of_measurement")
    if unit == "W":
        ceiling = state.attributes.get("max")
        if isinstance(ceiling, (int, float)) and 0 < ceiling <= 5000:
            return float(ceiling)
    registry_entry = er.async_get(hass).async_get(entity_id)
    if registry_entry is None or not registry_entry.device_id:
        return None
    device = dr.async_get(hass).async_get(registry_entry.device_id)
    if device is None:
        return None
    model = (device.model or "").upper()
    if "M200G4" in model or "M200" in model:
        return 2000.0
    if "M80G4" in model or "M80" in model:
        return 800.0
    return None
