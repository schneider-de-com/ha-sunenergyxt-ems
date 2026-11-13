"""Classify Sprint 1 endpoint observations without inventing AC evidence.

This module is an acceptance-test aid, not a second EMS controller.  The live
adapter must establish that its values are fresh, the inverter feedback has
settled, the test is isolated to one BK215/M200, and full-charge feed-in is
disabled.  Requested power, the HA-reported limit and measured AC power remain
separate evidence; this classifier only decides whether a reported 0/100 %
limit fits the signed household grid power and battery availability.
"""

from dataclasses import dataclass
from math import isfinite

from tools.sprint1_monitor_prototype import Evidence


@dataclass(frozen=True)
class EndpointContext:
    """Fresh context for one reported inverter-limit observation."""

    limit_pct: float
    grid_w: float
    grid_target_w: float
    grid_fresh: bool | None
    limit_fresh: bool | None
    controller_enabled: bool | None
    settled: bool | None
    isolated: bool | None
    feed_in: bool | None
    reserve_ok: bool | None
    inverter_available: bool | None


def classify_endpoint(value: EndpointContext) -> Evidence:
    """Return a conservative decision for an exact 0 or 100 percent report.

    Positive grid power is import.  No numerical error band is introduced:
    comparisons use the configured 20 W target itself.  Persistence and sample
    freshness are deployment concerns and must be established by the adapter.
    """

    numbers = (value.limit_pct, value.grid_w, value.grid_target_w)
    if not all(isinstance(number, (int, float)) and isfinite(number) for number in numbers):
        return Evidence.UNKNOWN
    if value.limit_pct not in (0, 100):
        return Evidence.UNKNOWN
    required = (
        value.grid_fresh,
        value.limit_fresh,
        value.controller_enabled,
        value.settled,
        value.isolated,
        value.inverter_available,
    )
    if not all(flag is True for flag in required) or value.feed_in is not False:
        return Evidence.UNKNOWN

    if value.limit_pct == 0:
        if value.reserve_ok is False:
            return Evidence.FITS
        if value.reserve_ok is not True:
            return Evidence.UNKNOWN
        return Evidence.MISMATCH if value.grid_w > value.grid_target_w else Evidence.FITS

    # At the upper endpoint, continued import at or above target means the
    # household can use maximum output.  Falling below target means maximum
    # output conflicts with household-load control while feed-in is disabled.
    return Evidence.MISMATCH if value.grid_w < value.grid_target_w else Evidence.FITS
