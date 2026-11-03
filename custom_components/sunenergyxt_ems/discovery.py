"""Suggest entities from stable registry metadata, not user-renamable entity names."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Candidate:
    entity_id: str
    platform: str
    device_id: str | None
    source_domain: str
    translation_key: str | None
    unit: str | None


_BK_ROLES = {
    "wifi_soc": "soc",
    "wifi_cnfg_disc_soc_min": "lower",
    "wifi_cnfg_chg_soc_max": "upper",
    "wifi_inputpower_total": "charging",
    "wifi_outputpower_total": "dc_output",
}


def find_batteries(entities: Iterable[Candidate]) -> dict[str, dict[str, str]]:
    """Match BK215 sensor/limit entities from the official SunEnergyXT integration."""
    devices: dict[str, dict[str, str]] = {}
    for item in entities:
        if item.source_domain.lower() != "sunenergyxt" or not item.device_id:
            continue
        role = _BK_ROLES.get(item.translation_key or "")
        if (
            role
            and item.unit in (None, "W" if role == "charging" else "%")
            and item.platform == ("number" if role in ("lower", "upper") else "sensor")
        ):
            devices.setdefault(item.device_id, {})[role] = item.entity_id
    return {device_id: roles for device_id, roles in devices.items() if "soc" in roles}


def suggest_inverter_limit(entities: Iterable[Candidate], device_id: str) -> str | None:
    """Suggest a writable inverter limit; allow manual correction in the UI."""
    for item in entities:
        if (
            item.device_id == device_id
            and item.platform == "number"
            and item.unit in ("%", "W")
            and item.translation_key
            and any(
                key in item.translation_key.lower()
                for key in ("active_power_regulation", "power_limit", "output_limit")
            )
        ):
            return item.entity_id
    return None


def suggest_grid(entities: Iterable[Candidate]) -> str | None:
    """Prefer Home Assistant Energy's combined grid power; never a phase or kWh."""
    valid = [
        item
        for item in entities
        if item.platform == "sensor"
        and item.unit in ("W", "kW")
        and (item.translation_key or "").lower()
        in (
            "total_active_power",
            "grid_power",
            "net_power",
            "active_power_total",
        )
    ]
    # The HA Energy integration marks a *signed* combined meter with a
    # dedicated unique ID. Its entity ID may be arbitrarily renamed.
    energy = [item for item in valid if item.source_domain.lower() == "energy"]
    if len(energy) == 1:
        return energy[0].entity_id
    if energy:
        return None  # Multiple combined meters: ask the user.
    return valid[0].entity_id if len(valid) == 1 else None
