"""Conservative offline classification examples; no live control."""

from dataclasses import replace

import pytest

from tools.sprint1_endpoint_evidence import EndpointContext, classify_endpoint
from tools.sprint1_monitor_prototype import Evidence


@pytest.fixture
def context():
    return EndpointContext(
        limit=0, grid_w=200, grid_target_w=20, filter_w=30,
        grid_fresh=True, limit_fresh=True, enabled=True, settled=True,
        isolated=True, feed_in=False, reserve_ok=True, inverter_available=True,
        other_generation_absent=True, measured_ac_w=0, ac_fresh=True,
    )


def test_zero_with_unmet_load_and_available_energy_is_mismatch(context):
    assert classify_endpoint(context) is Evidence.MISMATCH


def test_zero_near_target_or_reserve_exhausted_is_not_mismatch(context):
    assert classify_endpoint(replace(context, grid_w=20)) is Evidence.FITS
    assert classify_endpoint(replace(context, reserve_ok=False)) is Evidence.FITS


def test_zero_with_stale_or_nonzero_ac_is_unknown(context):
    assert classify_endpoint(replace(context, ac_fresh=False)) is Evidence.UNKNOWN
    assert classify_endpoint(replace(context, measured_ac_w=50)) is Evidence.UNKNOWN


def test_hundred_with_sustained_excess_and_attributable_ac_is_mismatch(context):
    value = replace(context, limit=100, grid_w=-200, measured_ac_w=1200)
    assert classify_endpoint(value) is Evidence.MISMATCH


def test_hundred_when_importing_cannot_prove_limit_fits(context):
    value = replace(context, limit=100, grid_w=300, measured_ac_w=1200)
    assert classify_endpoint(value) is Evidence.UNKNOWN


@pytest.mark.parametrize("field", ["grid_fresh", "limit_fresh", "enabled", "settled", "isolated"])
def test_missing_common_context_is_unknown(context, field):
    assert classify_endpoint(replace(context, **{field: False})) is Evidence.UNKNOWN


@pytest.mark.parametrize("field", ["other_generation_absent", "ac_fresh"])
def test_hundred_without_attribution_is_unknown(context, field):
    value = replace(context, limit=100, grid_w=-200, measured_ac_w=1200, **{field: False})
    assert classify_endpoint(value) is Evidence.UNKNOWN


@pytest.mark.parametrize("field", ["reserve_ok", "inverter_available"])
def test_zero_without_eligibility_information_is_unknown(context, field):
    assert classify_endpoint(replace(context, **{field: None})) is Evidence.UNKNOWN


def test_feed_in_enabled_or_inconsistent_export_cannot_be_classified(context):
    value = replace(context, limit=100, grid_w=-200, measured_ac_w=100)
    assert classify_endpoint(value) is Evidence.UNKNOWN
    assert classify_endpoint(replace(value, measured_ac_w=1200, feed_in=True)) is Evidence.UNKNOWN


@pytest.mark.parametrize("field", ["limit", "grid_w", "grid_target_w", "filter_w", "measured_ac_w"])
def test_nonfinite_numbers_are_unknown(context, field):
    assert classify_endpoint(replace(context, **{field: float("nan")})) is Evidence.UNKNOWN
