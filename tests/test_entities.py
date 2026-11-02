"""Test exposed Home Assistant switches and status without live devices."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import sensor as sensors  # noqa: E402
from custom_components.sunenergyxt_ems import switch as switches  # noqa: E402


@pytest.fixture
def objects():
    entry = SimpleNamespace(
        entry_id="ems-entry",
        options={"enabled": True, "feed_in": False},
    )
    runtime = SimpleNamespace(
        status="waiting",
        last_error=None,
        signal="ems_updated",
    )
    changes = []
    def change_entry(config_entry, *, options):
        config_entry.options = options
        changes.append(options.copy())
    runtime.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=change_entry)
    )
    return entry, runtime, changes


def test_positive_switch_reflects_persisted_initial_state(objects):
    entry, runtime, _ = objects
    assert switches.EMSToggle(entry, runtime, "enabled", "EMS control").is_on
    assert not switches.EMSToggle(entry, runtime, "feed_in", "Feed-in").is_on


@pytest.mark.asyncio
async def test_positive_feed_in_switch_updates_only_its_setting(objects, monkeypatch):
    entry, runtime, changes = objects
    entity = switches.EMSToggle(entry, runtime, "feed_in", "Feed-in")
    monkeypatch.setattr(entity, "async_write_ha_state", lambda: None)
    await entity.async_turn_on()
    assert entity.is_on
    assert changes == [{"enabled": True, "feed_in": True}]
    await entity.async_turn_off()
    assert not entity.is_on
    assert entry.options["enabled"] is True


@pytest.mark.asyncio
async def test_negative_manual_disable_updates_persistent_setting(objects, monkeypatch):
    entry, runtime, changes = objects
    entity = switches.EMSToggle(entry, runtime, "enabled", "EMS control")
    monkeypatch.setattr(entity, "async_write_ha_state", lambda: None)
    await entity.async_turn_off()
    assert entry.options["enabled"] is False


def test_positive_status_sensor_reports_waiting_and_running(objects):
    entry, runtime, _ = objects
    entity = sensors.EMSStatus(entry, runtime)
    assert entity.native_value == "waiting"
    runtime.status = "running"
    assert entity.native_value == "running"
    assert entity.extra_state_attributes is None


def test_negative_status_sensor_exposes_write_failure(objects):
    entry, runtime, _ = objects
    runtime.status = "write_error"
    runtime.last_error = "number.inverter: unavailable"
    entity = sensors.EMSStatus(entry, runtime)
    assert entity.native_value == "write_error"
    assert entity.extra_state_attributes == {
        "last_error": "number.inverter: unavailable"
    }


@pytest.mark.asyncio
async def test_positive_switch_setup_creates_two_entities(objects):
    entry, runtime, _ = objects
    hass = SimpleNamespace(data={"sunenergyxt_ems": {"ems-entry": runtime}})
    created = []
    await switches.async_setup_entry(hass, entry, created.extend)
    assert len(created) == 2
    assert {entity.unique_id for entity in created} == {
        "ems-entry_enabled", "ems-entry_feed_in"
    }


@pytest.mark.asyncio
async def test_positive_sensor_setup_creates_status(objects):
    entry, runtime, _ = objects
    hass = SimpleNamespace(data={"sunenergyxt_ems": {"ems-entry": runtime}})
    created = []
    await sensors.async_setup_entry(hass, entry, created.extend)
    assert len(created) == 1
    assert created[0].unique_id == "ems-entry_status"
