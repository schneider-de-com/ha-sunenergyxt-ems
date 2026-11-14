"""Historical forecasts survive restart without becoming fresh planning inputs."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import forecast_evidence as module  # noqa: E402

NOW = datetime(2026, 11, 14, tzinfo=UTC)


def snapshot(at=NOW, owner="owner"):
    return {
        "config_entry_id": owner,
        "received_at": at.isoformat(),
        "configuration_updated_at": None,
        "periods": [[at.isoformat(), (at + timedelta(hours=1)).isoformat(), 100.0]],
    }


@pytest.fixture
def storage(monkeypatch):
    class MemoryStore:
        data = None
        fail = False

        async def async_load(self):
            return deepcopy(self.data)

        async def async_save(self, data):
            if self.fail:
                raise OSError("disk unavailable")
            self.data = deepcopy(data)

    store = MemoryStore()
    monkeypatch.setattr(module, "Store", lambda *a, **kw: store)
    monkeypatch.setattr(module.dt_util, "utcnow", lambda: NOW)
    return store


async def start():
    history = module.ForecastEvidence(SimpleNamespace(), "ems", lambda: None)
    await history.async_start()
    return history


async def test_snapshot_survives_restart_and_duplicates_do_not_rewrite_history(storage):
    first = await start()
    await first.async_record(snapshot())
    saved = deepcopy(storage.data)
    await first.async_record(snapshot())
    assert storage.data == saved
    second = await start()
    assert second.summary()["snapshot_count"] == 1
    assert second.summary()["latest_received_at"] == NOW.isoformat()
    assert second.summary()["semantics"] == "historical_point_forecasts_not_live_eligibility"
    assert "periods" not in second.summary()
    assert storage.data["snapshots"][0] == snapshot()
    assert second.persistence_ok


async def test_failed_save_or_readback_is_visible_without_overwriting_corrupt_evidence(storage):
    history = await start()
    storage.fail = True
    await history.async_record(snapshot())
    assert history.persistence_ok is False
    assert history.storage_error == "forecast_evidence_write_failed"
    storage.fail = False
    storage.data = {"version": 1, "snapshots": [{"bad": "payload"}]}
    history = await start()
    before = deepcopy(storage.data)
    await history.async_record(snapshot())
    assert history.persistence_ok is False
    assert storage.data == before


@pytest.mark.parametrize("bad", ["future", "nan", "overlap", "secret", "naive"])
async def test_invalid_forecast_evidence_never_becomes_durable(storage, bad):
    history = await start()
    data = snapshot()
    if bad == "future":
        data["received_at"] = (NOW + timedelta(seconds=1)).isoformat()
    elif bad == "nan":
        data["periods"][0][2] = float("nan")
    elif bad == "overlap":
        data["periods"] *= 2
    elif bad == "secret":
        data["api_key"] = "must_not_be_saved"
    else:
        data["periods"][0][0] = NOW.replace(tzinfo=None).isoformat()
    await history.async_record(data)
    assert history.persistence_ok is False
    assert storage.data is None


async def test_retention_is_bounded_and_keeps_separate_owner_provenance(storage):
    history = await start()
    old = snapshot(NOW - timedelta(days=15))
    await history.async_record(old)
    await history.async_record(snapshot(owner="other"))
    await history.async_record(snapshot())
    assert history.summary()["snapshot_count"] == 2
    assert {row["config_entry_id"] for row in storage.data["snapshots"]} == {"owner", "other"}


async def test_conflicting_same_receipt_is_not_silently_replaced(storage):
    history = await start()
    await history.async_record(snapshot())
    before = deepcopy(storage.data)
    different = snapshot()
    different["periods"][0][2] = 200.0
    await history.async_record(different)
    assert history.persistence_ok is False
    assert storage.data == before


async def test_failed_receipt_is_retained_when_next_owner_update_saves_successfully(storage):
    history = await start()
    storage.fail = True
    await history.async_record(snapshot())
    assert history.summary()["may_include_unconfirmed"] is True
    storage.fail = False
    await history.async_record(snapshot(owner="other"))
    assert len(storage.data["snapshots"]) == 2
    assert history.persistence_ok


async def test_readback_mismatch_does_not_claim_a_verified_checkpoint(storage, monkeypatch):
    history = await start()

    async def wrong():
        return None

    monkeypatch.setattr(storage, "async_load", wrong)
    await history.async_record(snapshot())
    assert not history.persistence_ok
    assert history.storage_error == "forecast_evidence_write_failed"


async def test_restored_old_records_are_not_current_retention(storage):
    storage.data = {"version": 1, "snapshots": [snapshot(NOW - timedelta(days=15))]}
    history = await start()
    assert history.summary()["snapshot_count"] == 0


async def test_capacity_bound_keeps_latest_2048_received_snapshots(storage):
    storage.data = {
        "version": 1,
        "snapshots": [snapshot(NOW - timedelta(seconds=offset)) for offset in range(2048, 0, -1)],
    }
    history = await start()
    await history.async_record(snapshot())
    assert len(storage.data["snapshots"]) == 2048
    assert (
        storage.data["snapshots"][0]["received_at"] == (NOW - timedelta(seconds=2047)).isoformat()
    )
    assert history.summary()["latest_received_at"] == NOW.isoformat()
