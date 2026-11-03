"""Validate optional power sources without writing device state."""

from datetime import UTC, datetime, timedelta
from math import isfinite


def fresh_power(hass, source: str | None) -> float | None:
    if not source:
        return None
    state = hass.states.get(source)
    if state is None:
        return None
    stamp = getattr(state, "last_reported", None) or state.last_updated
    age = datetime.now(UTC) - stamp
    if age < timedelta(seconds=-5) or age > timedelta(seconds=90):
        return None
    unit = state.attributes.get("unit_of_measurement")
    try:
        raw = float(state.state)
    except (TypeError, ValueError):
        return None
    if not isfinite(raw) or unit not in ("W", "kW"):
        return None
    watts = raw * 1000 if unit == "kW" else raw
    return watts if 0 <= watts <= 10000 else None


def ev3600_charging(hass, options: dict | None) -> bool | None:
    """False when not configured, otherwise do not guess on stale inputs."""
    if not options:
        return False
    mode = hass.states.get(options.get("mode"))
    if mode is None or mode.state not in ("on", "off"):
        return None
    stamp = getattr(mode, "last_reported", None) or mode.last_updated
    age = datetime.now(UTC) - stamp
    if age < timedelta(seconds=-5) or age > timedelta(seconds=90):
        return None
    if mode.state == "off":
        return False
    watts = fresh_power(hass, options.get("power"))
    return watts > 0 if watts is not None else None
