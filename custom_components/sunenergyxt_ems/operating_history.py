"""Bounded minute evidence from power samples, with per-channel coverage."""

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timedelta
from math import isclose
from typing import TypedDict

from .forecast import ForecastError, _utc


class Channel(TypedDict):
    """Integrated positive/negative Wh only over supported sample coverage."""

    positive_wh: float
    negative_wh: float
    covered_seconds: float


class Minute(TypedDict):
    """Partial minutes are valid and never imply full temporal coverage."""

    start: str
    elapsed_seconds: float
    channels: dict[str, Channel]
    states: dict[str, float]


@dataclass(frozen=True, slots=True)
class OperatingSample:
    """Validated cached power channels in W, and the observed controller state."""

    at: datetime
    watts: Mapping[str, float | None]
    state: str


def _number(value: object, lower: float, upper: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not lower <= value <= upper
    ):
        raise ForecastError("invalid_operating_evidence")
    return float(value)


class OperatingHistory:
    """Integrate held power for <=15s between valid endpoints; never across restart.

    This is an estimate from cached power, not a certified energy counter or
    proof of device response. Missing endpoints make that channel uncovered.
    """

    def __init__(self) -> None:
        self._rows: list[Minute] = []
        self._last: OperatingSample | None = None
        self._seen_until: datetime | None = None

    @property
    def rows(self) -> tuple[Minute, ...]:
        return tuple(deepcopy(self._rows))

    def summary(self) -> dict[str, object]:
        """Summarize retained coverage; callers run the full scan off the event loop."""
        channels: dict[str, Channel] = {}
        states: dict[str, float] = {}
        for row in self._rows:
            for key, value in row["channels"].items():
                total = channels.setdefault(
                    key, {"positive_wh": 0.0, "negative_wh": 0.0, "covered_seconds": 0.0}
                )
                total["positive_wh"] += value["positive_wh"]
                total["negative_wh"] += value["negative_wh"]
                total["covered_seconds"] += value["covered_seconds"]
            for state, seconds in row["states"].items():
                states[state] = states.get(state, 0.0) + seconds
        return {
            "semantics": "integrated_cached_power_estimate_not_energy_meter_or_savings",
            "minute_rows": len(self._rows),
            "first_minute": self._rows[0]["start"] if self._rows else None,
            "seen_until": self._seen_until.isoformat() if self._seen_until else None,
            "retained_elapsed_seconds": sum(row["elapsed_seconds"] for row in self._rows),
            "channels": channels,
            "states": states,
            "recent_rows": deepcopy(self._rows[-5:]),
        }

    def break_coverage(self) -> None:
        """Source changes invalidate the next integration, not stored evidence."""
        self._last = None

    def observe(self, sample: OperatingSample) -> None:
        at = _utc(sample.at)
        if not isinstance(sample.state, str) or not sample.state:
            raise ForecastError("invalid_operating_evidence")
        for key, value in sample.watts.items():
            if not isinstance(key, str) or not key:
                raise ForecastError("invalid_operating_evidence")
            if value is not None:
                _number(value, -100_000, 100_000)
        if self._seen_until is not None and at < self._seen_until:
            raise ForecastError("operating_clock_reversed")
        cutoff = at - timedelta(days=14)
        if self._seen_until is not None:
            previous = self._last
            covered = previous is not None and at - self._seen_until <= timedelta(seconds=15)
            cursor = max(self._seen_until, cutoff)
            while cursor < at:
                minute = cursor.replace(second=0, microsecond=0)
                end = min(at, minute + timedelta(minutes=1))
                seconds = (end - cursor).total_seconds()
                key = minute.isoformat()
                if not self._rows or self._rows[-1]["start"] != key:
                    self._rows.append(
                        {"start": key, "elapsed_seconds": 0.0, "channels": {}, "states": {}}
                    )
                row = self._rows[-1]
                row["elapsed_seconds"] = round(row["elapsed_seconds"] + seconds, 6)
                state = previous.state if covered and previous is not None else "observer_gap"
                row["states"][state] = round(row["states"].get(state, 0.0) + seconds, 6)
                if covered and previous is not None:
                    for channel, power in previous.watts.items():
                        if power is None or sample.watts.get(channel) is None:
                            continue
                        accumulator = row["channels"].setdefault(
                            channel,
                            {"positive_wh": 0.0, "negative_wh": 0.0, "covered_seconds": 0.0},
                        )
                        accumulator["positive_wh"] += max(0, power) * seconds / 3600
                        accumulator["negative_wh"] += max(0, -power) * seconds / 3600
                        accumulator["covered_seconds"] = round(
                            accumulator["covered_seconds"] + seconds, 6
                        )
                cursor = end
        cutoff_key = cutoff.replace(second=0, microsecond=0).isoformat()
        self._rows = [row for row in self._rows if row["start"] >= cutoff_key]
        self._last, self._seen_until = deepcopy(sample), at

    def as_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "seen_until": self._seen_until.isoformat() if self._seen_until else None,
            "rows": deepcopy(self._rows),
        }

    @classmethod
    def from_dict(cls, payload: object, *, now: datetime) -> OperatingHistory:
        """Validate stored quantities; restore with an explicit coverage break."""
        if (
            not isinstance(payload, Mapping)
            or type(payload.get("version")) is not int
            or payload.get("version") != 1
        ):
            raise ForecastError("invalid_operating_store")
        raw_rows, raw_until = payload.get("rows"), payload.get("seen_until")
        if (
            not isinstance(raw_rows, list)
            or len(raw_rows) > 14 * 24 * 60 + 1
            or (raw_until is not None and not isinstance(raw_until, str))
        ):
            raise ForecastError("invalid_operating_store")
        history = cls()
        until = _utc(datetime.fromisoformat(raw_until)) if raw_until is not None else None
        now = _utc(now)
        if (until is not None and until > now) or (until is None and raw_rows):
            raise ForecastError("invalid_operating_store")
        previous: datetime | None = None
        for raw in raw_rows:
            if not isinstance(raw, Mapping) or not isinstance(raw.get("start"), str):
                raise ForecastError("invalid_operating_store")
            start = _utc(datetime.fromisoformat(raw["start"]))
            if (
                start.second
                or start.microsecond
                or until is None
                or start > until
                or (previous is not None and start <= previous)
            ):
                raise ForecastError("invalid_operating_store")
            elapsed = _number(raw.get("elapsed_seconds"), 0, 60)
            if elapsed > (until - start).total_seconds() + 1e-7:
                raise ForecastError("invalid_operating_store")
            channels, states = raw.get("channels"), raw.get("states")
            if not isinstance(channels, Mapping) or not isinstance(states, Mapping):
                raise ForecastError("invalid_operating_store")
            clean_states: dict[str, float] = {}
            for key, value in states.items():
                if not isinstance(key, str) or not key:
                    raise ForecastError("invalid_operating_store")
                clean_states[key] = _number(value, 0, elapsed)
            if not isclose(sum(clean_states.values()), elapsed, abs_tol=1e-7):
                raise ForecastError("invalid_operating_store")
            clean_channels: dict[str, Channel] = {}
            for key, value in channels.items():
                if not isinstance(key, str) or not key or not isinstance(value, Mapping):
                    raise ForecastError("invalid_operating_store")
                seconds = _number(value.get("covered_seconds"), 0, elapsed)
                positive = _number(value.get("positive_wh"), 0, 100_000 * seconds / 3600 + 1e-7)
                negative = _number(value.get("negative_wh"), 0, 100_000 * seconds / 3600 + 1e-7)
                if positive + negative > 100_000 * seconds / 3600 + 1e-7:
                    raise ForecastError("invalid_operating_store")
                clean_channels[key] = {
                    "positive_wh": positive,
                    "negative_wh": negative,
                    "covered_seconds": seconds,
                }
            history._rows.append(
                {
                    "start": start.isoformat(),
                    "elapsed_seconds": elapsed,
                    "channels": clean_channels,
                    "states": clean_states,
                }
            )
            previous = start
        history._seen_until = until
        return history
