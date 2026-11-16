from collections.abc import Mapping

"""Shared configuration keys for SunEnergyXT EMS."""

DOMAIN = "sunenergyxt_ems"
CONF_BATTERIES = "batteries"
CONF_GRID = "grid"
CONF_INVERTERS = "inverters"
CONF_SOC = "soc"
CONF_LOWER = "lower"
CONF_UPPER = "upper"
CONF_RATED_W = "rated_w"
CONF_DEVICE = "device_id"
CONF_GRID_INVERT = "invert_grid"
CONF_ENABLED = "enabled"
CONF_FEED_IN = "feed_in"
CONF_OUTPUT_LIMITS = "output_limit_w"
DEFAULT_GRID_TARGET_W = 20.0
# Backwards-compatible AC command budget for pre-Plus configurations; not a BMS/DC trip.
DEFAULT_TOWER_OUTPUT_LIMIT_W = 1920.0

CONF_FEED_IN_MODE = "feed_in_mode"
FEED_IN_MODE_HOUSEHOLD = "household"
FEED_IN_MODE_PV = "pv"
CONF_FEED_IN_HYSTERESIS = "feed_in_hysteresis_pct"


def configured_export_mode(options: Mapping[str, object]) -> str:
    """Read legacy settings as one effective, mutually exclusive export mode.

    A stored 'feed_in=True' with household mode never authorized intentional
    export. Keep that observed behavior during upgrades instead of promoting
    the stale boolean into PV-only feed-in.
    """
    enabled = options.get(CONF_FEED_IN, False) is True
    mode = options.get(CONF_FEED_IN_MODE, FEED_IN_MODE_PV)
    return FEED_IN_MODE_PV if enabled and mode == FEED_IN_MODE_PV else FEED_IN_MODE_HOUSEHOLD
