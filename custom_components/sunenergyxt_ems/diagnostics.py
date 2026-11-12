"""Home Assistant diagnostics for the EMS configuration."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_BATTERIES, CONF_GRID, CONF_GRID_INVERT, CONF_INVERTERS, DOMAIN

_BATTERY_FIELDS = ("device_id", "label", "soc", "lower", "upper", "charging", "dc_output")
_INVERTER_FIELDS = ("entity_id", "rated_w")


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Expose only the selected devices and measurement mappings, not arbitrary entry data."""
    batteries = []
    for battery in entry.data.get(CONF_BATTERIES, []):
        selected = {key: battery[key] for key in _BATTERY_FIELDS if key in battery}
        selected[CONF_INVERTERS] = [
            {key: inverter[key] for key in _INVERTER_FIELDS if key in inverter}
            for inverter in battery.get(CONF_INVERTERS, [])
        ]
        batteries.append(selected)
    result = {
        "configuration": {
            CONF_GRID: entry.data.get(CONF_GRID),
            CONF_GRID_INVERT: entry.data.get(CONF_GRID_INVERT, False),
            CONF_BATTERIES: batteries,
        }
    }
    runtime = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    observer = getattr(runtime, "observer", None)
    if observer is not None:
        result["demand_observation"] = {
            "reason": observer.reason,
            "persistence_ok": observer.persistence_ok,
            "storage_error": observer.storage_error,
            "last_poll_at": observer.last_poll_at.isoformat() if observer.last_poll_at else None,
            "complete_intervals": len(observer.history.intervals),
            "recent_quality_events": observer.events[-10:],
        }
        result["operating_evidence"] = observer.operating.summary()
    horizon = getattr(runtime, "horizon", None)
    if horizon is not None:
        decision = horizon.decision
        result["planning"] = {
            "reason": horizon.reason,
            "detail": horizon.detail,
            "horizon_until": horizon.horizon_until.isoformat() if horizon.horizon_until else None,
            "model_only": True,
            "expected_advantage_wh": decision.expected_advantage_wh if decision else None,
            "minimum_tested_advantage_wh": decision.minimum_tested_advantage_wh
            if decision
            else None,
            "priority": list(runtime.controller.planning_order),
        }
    return result
