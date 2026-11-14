"""Home Assistant diagnostics for the EMS configuration."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import CONF_BATTERIES, CONF_GRID, CONF_GRID_INVERT, CONF_INVERTERS, DOMAIN

_BATTERY_FIELDS = ("device_id", "label", "soc", "lower", "upper", "charging", "dc_output")
_INVERTER_FIELDS = ("entity_id", "rated_w")


def _ac_source_state(hass: HomeAssistant, source: str | None, at: datetime) -> dict[str, Any]:
    """Describe HA's cache without inferring physical sampling or copying arbitrary attributes."""
    state = hass.states.get(source) if source else None
    changed = state.last_changed if state else None
    updated = state.last_updated if state else None
    reported = state.last_reported if state else None
    return {
        "entity_id": source,
        "state_status": (state.state if state.state in ("unknown", "unavailable") else "present")
        if state
        else "missing",
        "power_unit_supported": state.attributes.get("unit_of_measurement") in ("W", "kW")
        if state
        else False,
        "last_changed": changed.isoformat() if changed else None,
        "last_updated": updated.isoformat() if updated else None,
        "last_reported": reported.isoformat() if reported else None,
        "value_change_age_seconds": (at - changed).total_seconds() if changed else None,
        "report_age_seconds": (at - reported).total_seconds() if reported else None,
        "device_sample_at": None,
    }


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
    at = dt_util.utcnow()
    mapping = entry.options.get("ac_power", {})
    if not isinstance(mapping, dict):
        mapping = {}
    sources = []
    for battery in batteries:
        for inverter in battery[CONF_INVERTERS]:
            source = mapping.get(inverter.get("entity_id"))
            sources.append(
                {
                    "inverter_entity_id": inverter.get("entity_id"),
                    **_ac_source_state(hass, source if isinstance(source, str) else None, at),
                }
            )
    result["measurement_sources"] = {
        "semantics": "ha_cached_state_timestamps_not_device_samples",
        "inspected_at": at.isoformat(),
        "ac_sources": sources,
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
        result["operating_evidence"] = await observer.async_operating_summary()
    horizon = getattr(runtime, "horizon", None)
    if horizon is not None:
        if (evidence := getattr(horizon, "forecast_evidence", None)) is not None:
            result["forecast_evidence"] = evidence.summary()
        result["forecast_sources"] = {
            "forecast_solar": [
                source.describe(at)
                for source in getattr(horizon, "forecast_solar_sources", {}).values()
                if source.entry_id is not None
            ]
        }
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
