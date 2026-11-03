"""Pure bounded PID correction. No Home Assistant dependencies or device writes."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PIDGains:
    kp_min: float = 0.5
    kp_max: float = 0.9
    kp_export: float = 1.0
    ki: float = 0.02
    kd: float = 0.12
    kd_limit: float = 120.0


@dataclass(frozen=True, slots=True)
class PIDTerms:
    """Observable control terms in watts."""

    proportional: float
    integral: float
    derivative: float

    @property
    def correction(self) -> float:
        return self.proportional + self.integral + self.derivative


def bounded_pid(
    error: float,
    previous_error: float | None,
    dt: float,
    integral: float,
    current_w: float,
    maximum_w: float,
    gains: PIDGains | None = None,
) -> PIDTerms:
    """Adapt gain to import error, limit integral windup and damp derivative.

    Preserve the agreed 0.5 import / 1.0 export gains for ordinary errors.
    Time dependence is applied only by the caller on a distinct meter sample.
    """
    gains = gains or PIDGains()
    gains = gains or PIDGains()
    strength = min(1.0, max(0.0, abs(error) - 200.0) / 600.0)
    kp = gains.kp_export if error < -20 else gains.kp_min + (gains.kp_max - gains.kp_min) * strength
    p = kp * error
    d = (
        max(-gains.kd_limit, min(gains.kd_limit, gains.kd * (error - previous_error) / dt))
        if previous_error is not None and dt > 0
        else 0.0
    )
    new_i = integral
    if dt > 0 and maximum_w > 0:
        candidate = max(-0.30 * maximum_w, min(0.30 * maximum_w, integral + gains.ki * error * dt))
        target = current_w + p + candidate + d
        if not ((target <= 0 and error < 0) or (target >= maximum_w and error > 0)):
            new_i = candidate
    return PIDTerms(p, new_i, d)
