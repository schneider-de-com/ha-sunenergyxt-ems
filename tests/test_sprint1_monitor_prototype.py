"""Offline examples for a monitoring prototype; no live device interaction."""

import pytest

from tools.sprint1_monitor_prototype import Evidence, Monitor, Snapshot


def sample(at, limit=0, evidence=Evidence.FITS, *, fresh=True, reserve=True, enabled=True):
    return Snapshot(at, limit, evidence, fresh, reserve, enabled)


@pytest.mark.parametrize("enabled", [False, None])
def test_manual_off_or_unknown_cannot_be_mistaken_for_endpoint_failure_or_pass(tmp_path, enabled):
    path = tmp_path / "monitor.db"
    monitor = Monitor(path, 0, 86400)
    assert monitor.observe(sample(60, 0, Evidence.MISMATCH, enabled=enabled)) == "continue"
    monitor.close()
    monitor = Monitor(path, 100, 86400)
    assert monitor.observe(sample(86400, 40)) == "continue"
    assert monitor.result() == "inconclusive"


def test_mismatch_at_either_endpoint_aborts_and_remains_aborted_after_restart(tmp_path):
    for limit in (0, 100):
        path = tmp_path / f"{limit}.db"
        first = Monitor(path, 0, 60)
        assert first.observe(sample(1, limit, Evidence.MISMATCH)) == "stop"
        first.close()
        second = Monitor(path, 999, 60)
        assert second.result() == "aborted"
        assert second.observe(sample(2)) == "stop"


def test_valid_endpoint_does_not_stop(tmp_path):
    monitor = Monitor(tmp_path / "monitor.db", 0, 60)
    assert monitor.observe(sample(1, 0)) == "continue"
    assert monitor.observe(sample(2, 100)) == "continue"
    assert monitor.result() == "running"


@pytest.mark.parametrize("fresh,reserve", [(False, True), (True, False)])
def test_stale_or_missing_safety_context_cannot_be_called_mismatch(tmp_path, fresh, reserve):
    monitor = Monitor(tmp_path / "monitor.db", 0, 86400)
    assert monitor.observe(sample(86400, 0, Evidence.MISMATCH, fresh=fresh, reserve=reserve)) == "continue"
    assert monitor.result() == "inconclusive"


@pytest.mark.parametrize("field", ["fresh", "reserve"])
@pytest.mark.parametrize("invalid", [None, "false", 1])
def test_unconfirmed_context_cannot_abort_or_pass(tmp_path, field, invalid):
    monitor = Monitor(tmp_path / "monitor.db", 0, 86400)
    context = {field: invalid}
    assert monitor.observe(sample(86400, 0, Evidence.MISMATCH, **context)) == "continue"
    assert monitor.result() == "inconclusive"


def test_unknown_endpoint_does_not_silently_pass(tmp_path):
    monitor = Monitor(tmp_path / "monitor.db", 0, 86400)
    assert monitor.observe(sample(86400, 100, Evidence.UNKNOWN)) == "continue"
    assert monitor.result() == "inconclusive"


@pytest.mark.parametrize("invalid", [None, "fits", "mismatch", "unknown"])
def test_unrecognized_endpoint_evidence_cannot_silently_pass(tmp_path, invalid):
    monitor = Monitor(tmp_path / "monitor.db", 0, 86400)
    assert monitor.observe(sample(86400, 100, invalid)) == "continue"
    assert monitor.result() == "inconclusive"


def test_restart_within_nominal_gap_is_still_unobserved(tmp_path):
    path = tmp_path / "monitor.db"
    first = Monitor(path, 0, 86400)
    assert first.observe(sample(60, 42)) == "continue"
    first.close()
    restarted = Monitor(path, 70, 86400)
    assert restarted.observe(sample(86400, 42)) == "continue"
    assert restarted.result() == "inconclusive"


@pytest.mark.parametrize("limit", [None, float("nan"), -1, 101])
def test_invalid_limit_cannot_count_as_covered(tmp_path, limit):
    monitor = Monitor(tmp_path / "monitor.db", 0, 86400)
    assert monitor.observe(sample(86400, limit, Evidence.MISMATCH)) == "continue"
    assert monitor.result() == "inconclusive"


def test_restarts_do_not_reset_start_or_hide_a_gap(tmp_path):
    path = tmp_path / "monitor.db"
    first = Monitor(path, 0, 60)
    first.observe(sample(30))
    first.close()
    second = Monitor(path, 43200, 60)
    second.observe(sample(86400))
    assert second.result() == "inconclusive"


def test_full_24_hours_are_required_and_no_endpoints_need_fail(tmp_path):
    monitor = Monitor(tmp_path / "monitor.db", 0, 3600)
    for at in range(3600, 86400, 3600):
        monitor.observe(sample(at, 42, Evidence.UNKNOWN))
    assert monitor.result() == "running"
    monitor.observe(sample(86400, 42, Evidence.UNKNOWN))
    assert monitor.result() == "eligible_for_review"


def test_out_of_order_observation_is_not_accepted(tmp_path):
    monitor = Monitor(tmp_path / "monitor.db", 0, 60)
    monitor.observe(sample(10))
    with pytest.raises(ValueError):
        monitor.observe(sample(9, 0, Evidence.MISMATCH))


def test_nonfinite_timestamp_is_not_accepted(tmp_path):
    monitor = Monitor(tmp_path / "monitor.db", 0, 60)
    with pytest.raises(ValueError):
        monitor.observe(sample(float("nan")))


def test_raw_requested_reported_and_physical_evidence_is_retained(tmp_path):
    monitor = Monitor(tmp_path / "monitor.db", 0, 60)
    snapshot = Snapshot(
        at=5,
        limit_pct=42,
        evidence=Evidence.UNKNOWN,
        meter_fresh=True,
        reserve_known=True,
        controller_enabled=True,
        grid_w=21.5,
        requested_w=840,
        reported_w=840,
        measured_ac_w=None,
        soc_pct=55,
        reserve_pct=10,
        status="running",
    )

    monitor.observe(snapshot)

    row = monitor.db.execute(
        "SELECT grid_w,requested_w,reported_w,measured_ac_w,soc_pct,reserve_pct,status "
        "FROM observations WHERE at=5"
    ).fetchone()
    assert row == (21.5, 840, 840, None, 55, 10, "running")
