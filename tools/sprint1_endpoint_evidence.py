"""Conservative offline endpoint examples, not a deployed live watchdog.

The caller must independently establish freshness, settling, isolation and
absence of other generation. filter_w is a technical filter, not an agreed
acceptance tolerance. One observation does not establish persistence.
"""

from dataclasses import dataclass
from math import isfinite

from tools.sprint1_monitor_prototype import Evidence


@dataclass(frozen=True)
class EndpointContext:
    limit: float
    grid_w: float
    grid_target_w: float
    filter_w: float
    grid_fresh: bool | None
    limit_fresh: bool | None
    enabled: bool | None
    settled: bool | None
    isolated: bool | None
    feed_in: bool | None
    reserve_ok: bool | None
    inverter_available: bool | None
    other_generation_absent: bool | None
    measured_ac_w: float
    ac_fresh: bool | None


def classify_endpoint(value: EndpointContext) -> Evidence:
    """Classify only explicitly supported cases; missing context stays unknown."""
    nums = (value.limit, value.grid_w, value.grid_target_w, value.filter_w, value.measured_ac_w)
    if not all(isinstance(n, (int, float)) and isfinite(n) for n in nums):
        return Evidence.UNKNOWN
    if value.filter_w < 0 or value.measured_ac_w < 0 or value.limit not in (0, 100):
        return Evidence.UNKNOWN
    if not all(x is True for x in (
        value.grid_fresh, value.limit_fresh, value.enabled, value.settled, value.isolated
    )) or value.feed_in is not False:
        return Evidence.UNKNOWN
    if value.limit == 0:
        if value.ac_fresh is not True or value.measured_ac_w > value.filter_w:
            return Evidence.UNKNOWN
        if value.reserve_ok is False:
            return Evidence.FITS
        if value.reserve_ok is not True or value.inverter_available is not True:
            return Evidence.UNKNOWN
        if value.grid_w > value.grid_target_w + value.filter_w:
            return Evidence.MISMATCH
        return Evidence.FITS
    if value.other_generation_absent is not True or value.ac_fresh is not True:
        return Evidence.UNKNOWN
    if value.grid_w < -value.filter_w:
        # Export cannot be attributed to this inverter if it exceeds its AC.
        if -value.grid_w > value.measured_ac_w:
            return Evidence.UNKNOWN
        return Evidence.MISMATCH
    return Evidence.UNKNOWN
