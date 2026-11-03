"""Replay a recorded incident as controller input, with explicit limitations.

The samples were read from Home Assistant's recorder on 2026-11-03.
They are the observed response of the OLD controller, not an independent load
trajectory or a high-fidelity electrical plant model. They can validate
policy invariants, but never prove financial savings or exact physical output.
"""

from __future__ import annotations

import json
from bisect import bisect_right
from datetime import datetime
from pathlib import Path

from custom_components.sunenergyxt_ems.logic import Battery, Controller, Inverter

FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "incident_2026-11-03_0025_0053.json"
)


def _track(record: dict[str, object], entity: str) -> list[tuple[datetime, float | None]]:
    raw = record["tracks"][entity]
    return [(datetime.fromisoformat(ts), v) for ts, v in raw]


def _soc_at(
    readings: list[tuple[datetime, float | None]], when: datetime
) -> float | None:
    times = [instant for instant, _ in readings]
    pos = bisect_right(times, when) - 1
    return readings[pos][1] if pos >= 0 else None


def test_night_replay_preserves_reserve_and_limits_every_normal_step() -> None:
    """Real meter spikes must not defeat the bounded, reserve-aware controller."""
    incident = json.loads(FIXTURE.read_text(encoding="utf-8"))
    meter = _track(
        incident, "sensor.heizungsraum_shelly_pro_3em_total_active_power"
    )
    first_soc = _track(incident, "sensor.bk215_keller_speicherlevel")
    second_soc = _track(incident, "sensor.bk215_2_bk_speicherlevel")

    assert len(meter) == 311
    assert min(v for _, v in meter if v is not None) < -1000
    assert _soc_at(first_soc, meter[-1][0]) == 10
    assert _soc_at(second_soc, meter[-1][0]) == 10

    controller = Controller()
    controller.feed_in = False
    previous_total = 0.0
    positive_steps = 0
    for when, grid in meter:
        batteries = (
            Battery(
                "bk215-1",
                _soc_at(first_soc, when),
                10,
                100,
                (Inverter("m200", 2000),),
                output_limit_w=1920,
            ),
            Battery(
                "bk215-2",
                _soc_at(second_soc, when),
                10,
                100,
                (Inverter("m80", 800),),
                output_limit_w=1920,
            ),
        )
        output = controller.update(batteries, grid, when.timestamp())
        a, b = output["m200"], output["m80"]
        assert 0 <= a <= 1920
        assert 0 <= b <= 800
        if batteries[0].soc is None or batteries[0].soc <= 10:
            assert a == 0
        if batteries[1].soc is None or batteries[1].soc <= 10:
            assert b == 0

        total = a + b
        # The old, uncontrolled inverter ramp of 0→100% on an 800W M80
        # is not an acceptable *single normal* correction step.
        assert total - previous_total <= 520.0001
        if total > previous_total:
            positive_steps += 1
        previous_total = total

    assert positive_steps > 0
    assert controller.state.blocked == {"bk215-1", "bk215-2"}
    assert previous_total == 0.0


def test_fixture_retains_original_inverter_telemetry_not_simulated_success() -> None:
    """Keep real command history separate from what the policy simulation predicts."""
    incident = json.loads(FIXTURE.read_text(encoding="utf-8"))
    m80 = _track(
        incident, "number.heizungsraum_deye_m80_active_power_regulation"
    )
    observed_percent = [p for _, p in m80 if p is not None]
    assert len(m80) >= 80
    assert min(observed_percent) == 0
    assert max(observed_percent) == 100
    assert any(
        abs(next_value - value) >= 50
        for value, next_value in zip(observed_percent, observed_percent[1:], strict=False)
    )
