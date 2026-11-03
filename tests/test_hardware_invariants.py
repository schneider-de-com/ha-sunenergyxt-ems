"""Deterministic black-box invariants for the BK215 controller.

Hardware constraints come from the BK215/B215 manufacturer's user manual:
two DC outputs, 1,920 W COMBINED. These are not SunEnergyXT 500 limits.

The seeded inputs vary loads, inverter capacities and reserve settings.
Safety constraints must hold for every sample, independently of controller
gains and how main/support allocation is implemented.
"""

from __future__ import annotations

from math import isfinite
from random import Random

from custom_components.sunenergyxt_ems.logic import (
    Battery,
    Controller,
    ControllerState,
    Inverter,
)


def test_bk215_invariants_across_400_reproducible_operating_points() -> None:
    rng = Random(215_1920)
    for case in range(400):
        rated_1 = rng.choice((600.0, 800.0, 1000.0, 1500.0, 2000.0))
        rated_2 = rng.choice((600.0, 800.0, 1000.0, 1500.0, 2000.0))
        configured = rng.uniform(250.0, 2500.0)
        # A legitimate previously reported inverter limit: below each
        # inverter's rating AND the shared 1,920 W BK215 output limit.
        observed_1 = rng.uniform(0, min(rated_1, configured, 1920))
        observed_2 = rng.uniform(
            0, min(rated_2, max(0, min(configured, 1920) - observed_1))
        )
        grid_w = rng.uniform(-2000, 5000)
        battery = Battery(
            key="bk",
            soc=rng.uniform(20, 95),
            lower_soc=10.0,
            upper_soc=100.0,
            inverters=(Inverter("first", rated_1), Inverter("second", rated_2)),
            output_limit_w=configured,
        )
        engine = Controller(
            ControllerState(outputs={"first": observed_1, "second": observed_2})
        )
        output = engine.update((battery,), grid_w, case + 1)
        assert set(output) == {"first", "second"}
        assert all(isfinite(watts) for watts in output.values())
        assert 0 <= output["first"] <= rated_1
        assert 0 <= output["second"] <= rated_2
        assert sum(output.values()) <= min(1920.0, configured) + 1e-7, (
            "BK215 combined DC output may never exceed its documented limit"
        )
        assert (
            sum(output.values()) - observed_1 - observed_2 <= 520.000001
        ), "A normal controller step must not jump more than 520 W"


def test_two_independent_bk215_systems_do_not_share_one_1920_w_budget() -> None:
    """Each physical BK215 owns its output budget, not one combined estate cap."""
    first = Battery(
        key="bk1", soc=80, lower_soc=10, upper_soc=90,
        inverters=(Inverter("m200", 2000),), output_limit_w=1920,
    )
    second = Battery(
        key="bk2", soc=80, lower_soc=10, upper_soc=90,
        inverters=(Inverter("m80", 800),), output_limit_w=1920,
    )
    engine = Controller(ControllerState(outputs={"m200": 1500, "m80": 500}))
    outputs = engine.update((first, second), 5000, 1)
    assert outputs["m200"] <= 1920
    assert outputs["m80"] <= 800
    assert outputs["m200"] + outputs["m80"] > 1920, (
        "Two physically independent BK215 systems must not be capped as one"
    )
