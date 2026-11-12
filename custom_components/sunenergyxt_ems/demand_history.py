"""Capture continuously observed energy without bridging measurement gaps."""

from collections.abc import Mapping
from datetime import datetime, timedelta

from .demand_forecast import validated_demand_history
from .forecast import EnergyInterval, ForecastError, _utc


class DemandHistory:
    """Completed half-hours only; partial observations never survive a restart."""

    def __init__(self) -> None:
        self._intervals: list[EnergyInterval] = []
        self._last_at: datetime | None = None
        self._last_watts: float | None = None
        self._slot: datetime | None = None
        self._watt_microseconds = 0.0
        self._covered_microseconds = 0

    @property
    def intervals(self) -> tuple[EnergyInterval, ...]:
        """Return completed observations; gaps remain absent."""
        return tuple(self._intervals)

    def break_coverage(self) -> None:
        """Keep completed measurements and discard any held/partial observation."""
        self._last_watts = None
        self._slot = None
        self._watt_microseconds = 0.0
        self._covered_microseconds = 0

    def observe(self, at: datetime, watts: float | None) -> None:
        """Integrate the previous observation only across gaps of at most 90 s.

        Source validation belongs to the caller. None explicitly breaks coverage.
        Constant power is held between fresh samples, never through a long silence.
        """
        try:
            at = _utc(at)
        except ForecastError:
            self.break_coverage()
            raise
        if watts is not None and (
            isinstance(watts, bool)
            or not isinstance(watts, (int, float))
            or not 0 <= watts <= 100_000
        ):
            self.break_coverage()
            raise ForecastError("invalid_demand_power")
        if self._last_at is not None and at <= self._last_at:
            if at == self._last_at and watts == self._last_watts:
                return
            self.break_coverage()
            raise ForecastError("unordered_demand_sample")
        self._intervals = [s for s in self._intervals if s.end > at - timedelta(days=28)]
        if (
            watts is None
            or self._last_watts is None
            or self._last_at is None
            or at - self._last_at > timedelta(seconds=90)
        ):
            self.break_coverage()
        else:
            cursor = self._last_at
            while cursor < at:
                slot = cursor.replace(minute=cursor.minute // 30 * 30, second=0, microsecond=0)
                if self._slot != slot:
                    self._slot = slot
                    self._watt_microseconds = 0.0
                    self._covered_microseconds = 0
                boundary = slot + timedelta(minutes=30)
                end = min(boundary, at)
                duration = end - cursor
                # Preserve datetime's exact resolution. Floating seconds can
                # overstate a complete slot and invalidate its own saved state.
                micros = duration.seconds * 1_000_000 + duration.microseconds
                self._watt_microseconds += self._last_watts * micros
                self._covered_microseconds += micros
                if end == boundary and self._covered_microseconds == 1_800_000_000:
                    if not self._intervals or slot >= self._intervals[-1].end:
                        self._intervals.append(
                            EnergyInterval(slot, boundary, self._watt_microseconds / 3_600_000_000)
                        )
                cursor = end
        self._last_at, self._last_watts = at, watts

    def as_dict(self) -> dict[str, object]:
        """Serialize completed slots only; a caller must durably save the result."""
        return {
            "version": 1,
            "intervals": [
                {"start": s.start.isoformat(), "end": s.end.isoformat(), "energy_wh": s.energy_wh}
                for s in self._intervals
            ],
        }

    @classmethod
    def from_dict(cls, payload: object, *, now: datetime) -> DemandHistory:
        """Reject corrupt state and resume with no claim of coverage during downtime."""
        if (
            not isinstance(payload, Mapping)
            or type(payload.get("version")) is not int
            or payload["version"] != 1
        ):
            raise ForecastError("invalid_demand_history_store")
        rows = payload.get("intervals")
        if not isinstance(rows, list):
            raise ForecastError("invalid_demand_history_store")
        intervals: list[EnergyInterval] = []
        for row in rows:
            if not isinstance(row, Mapping):
                raise ForecastError("invalid_demand_history_store")
            start, end, value = row.get("start"), row.get("end"), row.get("energy_wh")
            if (
                not isinstance(start, str)
                or not isinstance(end, str)
                or not isinstance(value, (int, float))
            ):
                raise ForecastError("invalid_demand_history_store")
            try:
                intervals.append(
                    EnergyInterval(
                        datetime.fromisoformat(start), datetime.fromisoformat(end), value
                    )
                )
            except ValueError as exc:
                raise ForecastError("invalid_demand_history_store") from exc
        restored = cls()
        restored._intervals = list(validated_demand_history(intervals, now=now))
        return restored
