"""Local prototype: persist a 24 h monitoring decision; no Home Assistant I/O.

An upstream evidence adapter must determine whether an endpoint fits the load.
This prototype deliberately makes no such inference from a grid sensor.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from pathlib import Path


class Evidence(Enum):
    FITS = "fits"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Snapshot:
    at: float
    limit_pct: float | None
    evidence: Evidence
    meter_fresh: bool
    reserve_known: bool
    controller_enabled: bool | None = None


class Monitor:
    """Retain evidence and stop state across process restarts in SQLite.

    max_gap_s is a deployment parameter, not a user acceptance threshold.
    """

    def __init__(self, path: Path, started_at: float, max_gap_s: float) -> None:
        if not isfinite(started_at) or not isfinite(max_gap_s) or max_gap_s <= 0:
            raise ValueError("start and positive max_gap_s must be finite")
        self.db = sqlite3.connect(path)
        self.max_gap_s = max_gap_s
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS state (id INTEGER PRIMARY KEY CHECK (id = 1), "
            "started REAL NOT NULL, last REAL, gaps INTEGER NOT NULL, "
            "unknown INTEGER NOT NULL, aborted INTEGER NOT NULL)"
        )
        inserted = self.db.execute(
            "INSERT OR IGNORE INTO state VALUES (1, ?, NULL, 0, 0, 0)", (started_at,)
        )
        if inserted.rowcount == 0:
            # A restarted observer cannot establish what happened while absent.
            self.db.execute("UPDATE state SET gaps=gaps+1 WHERE id=1 AND last IS NOT NULL")
        self.db.commit()

    def observe(self, snap: Snapshot) -> str:
        """Return 'stop' only for evidenced, current endpoint mismatch."""
        with self.db:
            started, last, gaps, unknown, aborted = self.db.execute(
                "SELECT started,last,gaps,unknown,aborted FROM state WHERE id=1"
            ).fetchone()
            if aborted:
                return "stop"
            if not isfinite(snap.at) or snap.at < started or (last is not None and snap.at <= last):
                raise ValueError("out-of-order observation")
            anchor = started if last is None else last
            gaps += snap.at - anchor > self.max_gap_s
            limit_valid = (
                snap.limit_pct is not None
                and isfinite(snap.limit_pct)
                and 0 <= snap.limit_pct <= 100
            )
            endpoint = limit_valid and snap.limit_pct in (0, 100)
            current = (
                snap.meter_fresh
                and snap.reserve_known
                and limit_valid
                and snap.controller_enabled is True
            )
            undecidable = not current or (
                endpoint and snap.evidence not in (Evidence.FITS, Evidence.MISMATCH)
            )
            unknown += undecidable
            aborted = int(current and endpoint and snap.evidence is Evidence.MISMATCH)
            self.db.execute(
                "UPDATE state SET last=?,gaps=?,unknown=?,aborted=? WHERE id=1",
                (snap.at, gaps, unknown, aborted),
            )
        return "stop" if aborted else "continue"

    def result(self) -> str:
        """Elapsed wall time alone is insufficient for a passed test."""
        started, last, gaps, unknown, aborted = self.db.execute(
            "SELECT started,last,gaps,unknown,aborted FROM state WHERE id=1"
        ).fetchone()
        if aborted:
            return "aborted"
        if last is None or last - started < 24 * 3600:
            return "running"
        if gaps or unknown:
            return "inconclusive"
        return "eligible_for_review"

    def close(self) -> None:
        self.db.close()
