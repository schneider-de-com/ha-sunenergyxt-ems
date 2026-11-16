"""Suggest Home Assistant entities using registry identities, never entity ID names."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta
from math import isfinite

from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.util.dt import utcnow

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
    """Auto-select one complete provider; ambiguous configured sources need user choice."""
    solcast_roles = {
        "total_kwh_forecast_today": "today",
        "total_kwh_forecast_tomorrow": "tomorrow",
        "lastupdated": "updated",
    }
    solar_roles = {
        "energy_production_today": "today",
        "energy_production_tomorrow": "tomorrow",
    }
    groups: dict[tuple[str, str], dict[str, str]] = {}

    # Count configured providers even when their sensors are currently missing,
    # disabled or unavailable. A second independent forecast must never let the
    # first become an implicit user selection.
    entries = getattr(getattr(hass, "config_entries", None), "async_entries", None)
    if callable(entries):
        for provider in ("forecast_solar", "solcast_solar"):
            for entry in entries(provider):
                if getattr(entry, "disabled_by", None) is None:
                    groups.setdefault((provider, entry.entry_id), {})

    for entity in getattr(er.async_get(hass), "entities", {}).values():
        provider = entity.platform
        owner = entity.config_entry_id
        if (
            provider not in ("forecast_solar", "solcast_solar")
            or entity.domain != "sensor"
            or not owner
            or entity.disabled_by is not None
        ):
            continue
        group = groups.setdefault((provider, owner), {})
        unique_id = entity.unique_id or ""
        if provider == "solcast_solar":
            role = solcast_roles.get(unique_id)
        else:
            prefix = f"{owner}_"
            role = (
                solar_roles.get(unique_id[len(prefix) :]) if unique_id.startswith(prefix) else None
            )
            if role is not None:
                state = hass.states.get(entity.entity_id)
                if state is None or state.attributes.get("unit_of_measurement") not in (
                    "Wh",
                    "kWh",
                ):
                    continue
        if role is not None:
            group[role] = entity.entity_id

    if len(groups) != 1:
        return {}
    (provider, _), selected = next(iter(groups.items()))
    required = (
        {"today", "tomorrow"} if provider == "forecast_solar" else {"today", "tomorrow", "updated"}
    )
    return selected if set(selected) == required else {}


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


def registered_bk_hardware_sources(
    hass: HomeAssistant, soc_entity_id: str | None
) -> dict[str, str] | None:
    """Resolve BK215 BMS hardware limits from upstream owned registry IDs.

    None indicates a non-BK/manual SoC source. An empty or incomplete mapping
    for a recognized BK head must *not* fall back to its configurable charge
    target: the BMS hardware limit is authoritative even if missing.
    """
    if not isinstance(soc_entity_id, str):
        return None
    registry = er.async_get(hass)
    head = registry.async_get(soc_entity_id)
    if (
        head is None
        or head.platform.lower() not in BK_SOURCE_DOMAINS
        or head.domain != "sensor"
        or not head.config_entry_id
        or not head.device_id
        or head.unique_id != f"{head.config_entry_id}_wifi_soc"
        or head.disabled_by is not None
    ):
        return None
    roles = {
        "head_limit": "wifi_mbms_hw_limited_chg_soc_max",
        "head_soc": "wifi_b_actual_soc",
        "pv_input": "wifi_inputpower_total",
    }
    for index in range(1, 8):
        roles[f"extension_limit_{index}"] = f"wifi_sbms{index}_hw_limited_chg_soc_max"
        roles[f"extension_soc_{index}"] = f"wifi_b{index}_actual_soc"

    by_unique = {f"{head.config_entry_id}_{suffix}": role for role, suffix in roles.items()}
    sources: dict[str, str] = {}
    for item in getattr(registry, "entities", {}).values():
        if (
            item.platform.lower() != head.platform.lower()
            or item.domain != "sensor"
            or item.config_entry_id != head.config_entry_id
            or item.device_id != head.device_id
            or item.disabled_by is not None
        ):
            continue
        role = by_unique.get(item.unique_id)
        if role:
            sources[role] = item.entity_id
    return sources


# The BK215/215 Plus head and each confirmed compatible B215 extension
# contribute 2,150 Wh nominal, according to the supported product family.
# This is capacity metadata, not an independent usable-kWh measurement.
BK_MODULE_CAPACITY_WH = 2150


def _fresh_bk_percent(hass: HomeAssistant, source: str | None) -> float | None:
    """Accept only recently reported, numeric, correct-unit BMS telemetry."""
    if not source:
        return None
    state = hass.states.get(source)
    if state is None or state.attributes.get("unit_of_measurement") != "%":
        return None
    stamp = getattr(state, "last_reported", None) or getattr(state, "last_updated", None)
    if stamp is None:
        return None
    age = utcnow() - stamp
    if age < timedelta(seconds=-5) or age > timedelta(seconds=90):
        return None
    try:
        value = float(state.state)
    except TypeError, ValueError:
        return None
    return value if isfinite(value) and 0 <= value <= 100 else None


def suggested_bk_capacity_wh(hass: HomeAssistant, soc_entity_id: str | None) -> int | None:
    """Offer 2,150 Wh per *positively observed* BK module, never by name.

    One missing side of a module's SOC/hardware pair is ambiguous and
    prevents autodetection. Both unavailable means not positively detected;
    preserve a user's previously confirmed capacity rather than overwriting
    it with this lower observed count during later configuration edits.
    """
    sources = registered_bk_hardware_sources(hass, soc_entity_id)
    if sources is None:
        return None
    if (
        _fresh_bk_percent(hass, sources.get("head_soc")) is None
        or _fresh_bk_percent(hass, sources.get("head_limit")) is None
    ):
        return None
    modules = 1
    for index in range(1, 8):
        measured = _fresh_bk_percent(hass, sources.get(f"extension_soc_{index}"))
        hardware = _fresh_bk_percent(hass, sources.get(f"extension_limit_{index}"))
        if (measured is None) != (hardware is None):
            return None
        if measured is not None:
            modules += 1
    return modules * BK_MODULE_CAPACITY_WH


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


def registered_ac_voltage(hass: HomeAssistant, inverter_entity_id: str) -> str | None:
    """Locate this Deye profile's grid voltage by upstream registry identity.

    None means the model/profile has no validated voltage contract. An empty
    string means this *supported* profile has a missing/ambiguous source:
    fail closed rather than silently treating the logger as grid-connected.
    Only the installed deye_micro_53.yaml contract is covered; never infer
    a sensor from a renameable entity_id or a display name.
    """
    registry = er.async_get(hass)
    inverter = registry.async_get(inverter_entity_id)
    if (
        inverter is None
        or inverter.platform.lower() != "solarman"
        or getattr(inverter, "domain", "number") != "number"
        or not inverter.device_id
        or not inverter.config_entry_id
    ):
        return None
    entries = getattr(hass, "config_entries", None)
    if entries is None:
        return None
    source_entry = entries.async_get_entry(inverter.config_entry_id)
    if source_entry is None or source_entry.options.get("lookup_file") != "deye_micro_53.yaml":
        return None
    expected = f"{inverter.config_entry_id.lower()}_grid_voltage_sensor"
    matches = [
        entry.entity_id
        for entry in getattr(registry, "entities", {}).values()
        if entry.device_id == inverter.device_id
        and entry.config_entry_id == inverter.config_entry_id
        and entry.platform.lower() == "solarman"
        and entry.domain == "sensor"
        and entry.unique_id == expected
        and entry.disabled_by is None
    ]
    return matches[0] if len(matches) == 1 else ""


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
    """Read an actuator's rated AC watts from its own hardware contract.

    W controls may advertise a rated maximum directly. For a percent Deye
    control, read the exact Solarman-owned *same-device* Device sensor;
    %/100 alone never proves rated inverter output. No name matching.
    """
    state = hass.states.get(entity_id)
    if state is None or getattr(state, "state", None) in ("unknown", "unavailable"):
        return None
    unit = state.attributes.get("unit_of_measurement")
    if unit == "W":
        maximum = state.attributes.get("max")
        if (
            isinstance(maximum, (float, int))
            and not isinstance(maximum, bool)
            and isfinite(maximum)
            and 0 < maximum <= 5000
        ):
            return float(maximum)
        return None
    if unit != "%":
        return None

    registry = er.async_get(hass)
    control = registry.async_get(entity_id)
    if (
        control is None
        or control.platform.lower() != "solarman"
        or control.domain != "number"
        or not control.config_entry_id
        or not control.device_id
        or control.unique_id.lower()
        != f"{control.config_entry_id.lower()}_active_power_regulation_number"
    ):
        return None
    expected = f"{control.config_entry_id.lower()}_device_sensor"
    matches = [
        candidate.entity_id
        for candidate in getattr(registry, "entities", {}).values()
        if candidate.domain == "sensor"
        and candidate.platform.lower() == "solarman"
        and candidate.device_id == control.device_id
        and candidate.config_entry_id == control.config_entry_id
        and candidate.unique_id.lower() == expected
        and candidate.disabled_by is None
    ]
    if len(matches) != 1:
        return None
    device = hass.states.get(matches[0])
    if device is None or device.state != "Microinverter":
        return None
    rated = device.attributes.get("Rated Power")
    if (
        isinstance(rated, (float, int))
        and not isinstance(rated, bool)
        and isfinite(rated)
        and 0 < rated <= 5000
    ):
        return float(rated)
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
