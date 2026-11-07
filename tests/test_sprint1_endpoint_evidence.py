"""Acceptance examples for the Sprint 1 endpoint guard."""

from dataclasses import replace

import pytest

from tools.sprint1_endpoint_evidence import EndpointContext, classify_endpoint
from tools.sprint1_monitor_prototype import Evidence


@pytest.fixture
def context() -> EndpointContext:
    return EndpointContext(
        limit_pct=0,
        grid_w=200,
        grid_target_w=20,
        grid_fresh=True,
        limit_fresh=True,
        controller_enabled=True,
        settled=True,
        isolated=True,
        feed_in=False,
        reserve_ok=True,
        inverter_available=True,
    )


def test_zero_with_unmet_load_and_available_energy_is_mismatch(context):
    assert classify_endpoint(context) is Evidence.MISMATCH


def test_zero_at_target_or_when_reserve_blocks_discharge_fits(context):
    assert classify_endpoint(replace(context, grid_w=20)) is Evidence.FITS
    assert classify_endpoint(replace(context, reserve_ok=False)) is Evidence.FITS


def test_zero_with_unavailable_inverter_fits_but_unknown_availability_does_not(context):
    assert classify_endpoint(replace(context, inverter_available=False)) is Evidence.FITS
    assert classify_endpoint(replace(context, inverter_available=None)) is Evidence.UNKNOWN


def test_hundred_below_target_is_mismatch_and_continued_import_fits(context):
    upper = replace(context, limit_pct=100, grid_w=-1)
    assert classify_endpoint(upper) is Evidence.MISMATCH
    assert classify_endpoint(replace(upper, grid_w=20)) is Evidence.FITS
    assert classify_endpoint(replace(upper, grid_w=300)) is Evidence.FITS


@pytest.mark.parametrize(
    "field",
    ["grid_fresh", "limit_fresh", "controller_enabled", "settled", "isolated"],
)
def test_missing_common_context_is_unknown(context, field):
    assert classify_endpoint(replace(context, **{field: False})) is Evidence.UNKNOWN


def test_feed_in_enabled_cannot_be_judged_as_household_load_control(context):
    assert classify_endpoint(replace(context, feed_in=True)) is Evidence.UNKNOWN


@pytest.mark.parametrize("field", ["limit_pct", "grid_w", "grid_target_w"])
def test_nonfinite_numbers_are_unknown(context, field):
    assert classify_endpoint(replace(context, **{field: float("nan")})) is Evidence.UNKNOWN


def test_non_endpoint_limit_is_not_endpoint_evidence(context):
    assert classify_endpoint(replace(context, limit_pct=42)) is Evidence.UNKNOWN
