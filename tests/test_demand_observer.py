"""HA observation storage must preserve gaps without writing hardware."""

import asyncio
import threading
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import observer as module  # noqa: E402
from custom_components.sunenergyxt_ems.observer import DemandObserver, HouseholdObservation  # noqa: E402
from custom_components.sunenergyxt_ems.operating_history import OperatingSample  # noqa: E402

START = datetime(2026, 11, 8, 12, tzinfo=UTC)


@pytest.fixture
def setup_observer(monkeypatch):
    saved = {"value": None, "fail": False}

    class MemoryStore:
        async def async_load(self):
            if saved.get("load_fail"):
                raise OSError("cannot read existing evidence")
            return deepcopy(saved["value"])

        async def async_save(self, value):
            if saved["fail"]:
                raise OSError("disk unavailable")
            if not saved.get("ignore_save"):
                saved["value"] = deepcopy(value)

    monkeypatch.setattr(module, "Store", lambda *args, **kwargs: MemoryStore())
    monkeypatch.setattr(module.ir, "async_get", lambda hass: SimpleNamespace(issues={}))
    cancel = Mock()
    callbacks = []
    monkeypatch.setattr(
        module,
        "async_track_time_interval",
        lambda hass, cb, interval: callbacks.append(cb) or cancel,
    )
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: START)
    entry = SimpleNamespace(
        entry_id="observer-test",
        data={"grid": "sensor.grid", "batteries": [{"inverters": [{"entity_id": "number.inv"}]}]},
        options={"ac_power": {"number.inv": "sensor.ac"}},
    )
    sample = {"at": START, "watts": 600, "reason": "measured"}

    def create():
        return DemandObserver(
            SimpleNamespace(async_add_executor_job=lambda fn, *args: asyncio.to_thread(fn, *args)),
            entry,
            lambda: HouseholdObservation(sample["at"], sample["watts"], sample["reason"]),
        )

    return create, sample, saved, entry, callbacks, cancel


async def advance(observer, sample, *, start=START, minutes=30):
    for seconds in range(30, minutes * 60 + 1, 30):
        sample["at"] = start + timedelta(seconds=seconds)
        await observer.async_refresh(sample["at"])


@pytest.mark.asyncio
async def test_collected_energy_is_persisted_and_read_back_after_restart(
    setup_observer, monkeypatch
):
    create, sample, saved, _, _, _ = setup_observer
    observer = create()
    await observer.async_start()
    await advance(observer, sample)
    await observer.async_stop()
    assert saved["value"]["history"]["intervals"][0]["energy_wh"] == 300
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: sample["at"])
    restarted = create()
    await restarted.async_start()
    assert restarted.history.intervals[0].energy_wh == 300
    await restarted.async_stop()


@pytest.mark.asyncio
async def test_real_power_evidence_survives_entry_restart_without_bridging_downtime(
    setup_observer, monkeypatch
):
    create, sample, saved, _, _, _ = setup_observer
    observer = create()
    observer.read_operating = lambda at: OperatingSample(at, {"grid": 3600}, "disabled")
    await observer.async_start()
    sample["at"] += timedelta(seconds=5)
    await observer.async_refresh(sample["at"])
    await observer.async_stop()
    assert saved["value"]["operating_history"]["rows"][0]["channels"]["grid"]["positive_wh"] == 5
    sample["at"] += timedelta(minutes=2)
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: sample["at"])
    restarted = create()
    restarted.read_operating = observer.read_operating
    await restarted.async_start()
    assert sum(row["states"].get("observer_gap", 0) for row in restarted.operating.rows) == 120
    assert restarted.operating.rows[0]["channels"]["grid"]["positive_wh"] == 5
    assert restarted.persistence_ok
    await restarted.async_stop()


@pytest.mark.asyncio
async def test_evidence_checkpoint_copy_does_not_block_hardware_event_loop(
    setup_observer, monkeypatch
):
    create, _, _, _, _, _ = setup_observer
    observer = create()
    threads = []
    original = observer.operating.as_dict

    def traced():
        threads.append(threading.get_ident())
        return original()

    monkeypatch.setattr(observer.operating, "as_dict", traced)
    await observer.async_start()
    await observer.async_stop()
    assert threads and all(identity != threading.get_ident() for identity in threads)


@pytest.mark.asyncio
async def test_missing_ac_never_produces_household_zeroes(setup_observer):
    create, sample, saved, _, _, _ = setup_observer
    observer = create()
    sample.update(watts=None, reason="ac_unavailable")
    await observer.async_start()
    await advance(observer, sample, minutes=60)
    await observer.async_stop()
    assert observer.history.intervals == ()
    assert saved["value"]["events"][-1]["reason"] == "ac_unavailable"


@pytest.mark.asyncio
async def test_clock_gap_and_restart_are_recorded_as_observer_gaps(setup_observer, monkeypatch):
    create, sample, saved, _, _, _ = setup_observer
    observer = create()
    await observer.async_start()
    sample["at"] = START + timedelta(minutes=10)
    await observer.async_refresh(sample["at"])
    await observer.async_stop()
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: START + timedelta(hours=1))
    sample["at"] = START + timedelta(hours=1)
    restored = create()
    await restored.async_start()
    assert sum(e["reason"] == "observer_gap" for e in restored.events) == 2
    assert restored.history.intervals == ()
    await restored.async_stop()


@pytest.mark.asyncio
async def test_mapping_change_discards_old_training_not_the_gap_ledger(setup_observer):
    create, sample, _, entry, _, _ = setup_observer
    observer = create()
    await observer.async_start()
    await advance(observer, sample)
    entry.options["ac_power"]["number.inv"] = "sensor.other_ac"
    sample["at"] += timedelta(seconds=30)
    await observer.async_refresh(sample["at"])
    assert observer.history.intervals == ()
    assert any(e["reason"] == "source_changed" for e in observer.events)
    await observer.async_stop()


@pytest.mark.asyncio
async def test_save_failure_marks_storage_untrusted_and_recovers(setup_observer):
    create, sample, saved, _, _, _ = setup_observer
    observer = create()
    await observer.async_start()
    saved["fail"] = True
    await advance(observer, sample, minutes=1)
    assert observer.storage_error is not None
    assert not observer.persistence_ok
    saved["fail"] = False
    await advance(observer, sample, start=sample["at"], minutes=1)
    assert observer.persistence_ok
    await observer.async_stop()


@pytest.mark.asyncio
async def test_lifecycle_is_idempotent_and_stopped_callbacks_cannot_collect(setup_observer):
    create, sample, _, _, callbacks, cancel = setup_observer
    observer = create()
    await observer.async_start()
    await observer.async_start()
    assert len(callbacks) == 1
    await observer.async_stop()
    before = observer.last_poll_at
    await callbacks[0](START + timedelta(hours=1))
    assert observer.last_poll_at == before
    cancel.assert_called_once()


@pytest.mark.asyncio
async def test_offline_polls_do_not_fabricate_observer_outages(setup_observer):
    create, sample, _, _, _, _ = setup_observer
    sample.update(watts=None, reason="inverter_offline")
    observer = create()
    await observer.async_start()
    await advance(observer, sample, minutes=60)
    assert [e["reason"] for e in observer.events] == ["inverter_offline"]
    await observer.async_stop()


@pytest.mark.asyncio
async def test_unreadable_or_corrupt_store_is_not_overwritten(setup_observer):
    create, _, saved, _, _, _ = setup_observer
    saved.update(load_fail=True, value={"unknown": "preserve"})
    observer = create()
    await observer.async_start()
    assert not observer.persistence_ok
    await observer.async_stop()
    assert saved["value"] == {"unknown": "preserve"}
    saved["load_fail"] = False
    await observer.async_start()
    assert not observer.persistence_ok
    await observer.async_stop()
    assert saved["value"] == {"unknown": "preserve"}


@pytest.mark.asyncio
async def test_successful_save_without_matching_readback_is_not_trusted(setup_observer):
    create, sample, saved, _, _, _ = setup_observer
    observer = create()
    await observer.async_start()
    saved["ignore_save"] = True
    await advance(observer, sample, minutes=1)
    assert not observer.persistence_ok
    assert "readback" in observer.storage_error
    await observer.async_stop()


@pytest.mark.asyncio
async def test_source_change_during_restart_keeps_chronological_events(setup_observer, monkeypatch):
    create, sample, saved, entry, _, _ = setup_observer
    observer = create()
    await observer.async_start()
    await advance(observer, sample)
    await observer.async_stop()
    entry.options["ac_power"]["number.inv"] = "sensor.changed"
    sample["at"] += timedelta(hours=1)
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: sample["at"])
    restarted = create()
    await restarted.async_start()
    await restarted.async_stop()
    reread = create()
    await reread.async_start()
    assert reread.persistence_ok
    assert reread.history.intervals == ()
    assert [e["at"] for e in saved["value"]["events"]] == sorted(
        e["at"] for e in saved["value"]["events"]
    )
    await reread.async_stop()


@pytest.mark.asyncio
async def test_source_error_breaks_coverage_without_stopping_collection(setup_observer):
    create, sample, _, _, _, _ = setup_observer
    observer = create()
    await observer.async_start()
    read = observer.read

    def bad_read():
        raise ValueError("unusable source")

    observer.read = bad_read
    sample["at"] += timedelta(seconds=30)
    await observer.async_refresh(sample["at"])
    assert observer.reason == "observation_error"
    observer.read = read
    await advance(observer, sample, start=sample["at"], minutes=30)
    assert observer.reason == "measured"
    assert observer.history.intervals == ()
    await observer.async_stop()


@pytest.mark.asyncio
async def test_future_tolerated_source_time_cannot_create_future_energy(
    setup_observer, monkeypatch
):
    create, sample, saved, _, _, _ = setup_observer
    observer = create()
    await observer.async_start()
    await advance(observer, sample, minutes=29)
    sample["at"] = START + timedelta(minutes=30)
    wall_time = sample["at"] - timedelta(seconds=2)
    await observer.async_refresh(wall_time)
    await observer.async_stop()
    assert not observer.history.intervals
    assert saved["value"]["events"][-1]["reason"] == "source_time_ahead"
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: wall_time + timedelta(minutes=1))
    sample["at"] = wall_time + timedelta(minutes=1)
    restarted = create()
    await restarted.async_start()
    assert restarted.persistence_ok
    await restarted.async_stop()
