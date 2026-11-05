"""Behavioral regression for the household battery-level sensor (#43)."""

from math import inf, nan

import pytest

from custom_components.sunenergyxt_ems.logic import weighted_battery_level


def test_expansion_capacity_contributes_to_weighted_level():
    assert weighted_battery_level([(80, 4300), (20, 2150)]) == 60


@pytest.mark.parametrize(
    "readings",
    [
        [],
        [(80, None)],
        [(None, 4300)],
        [(101, 4300)],
        [(-1, 4300)],
        [(80, 0)],
        [(80, -1)],
        [(80, inf)],
        [(nan, 2150)],
        [(80, 4300), (None, 2150)],
    ],
)
def test_incomplete_or_invalid_inputs_never_produce_partial_level(readings):
    assert weighted_battery_level(readings) is None
