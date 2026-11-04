"""Test exposed Home Assistant switches and status without live devices."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import sensor as sensors  # noqa: E402
from custom_components.sunenergyxt_ems import switch as switches  # noqa: E402
from custom_components.sunenergyxt_ems.logic import Controller, StartHold  # noqa: E402


@pytest.fixture(autouse=True)
def mocked_device_registry(monkeypatch):
    """Only simple unit tests mock the registry; HA lifecycle uses the real registry."""
    monkeypatch.setattr(
        sensors.dr,
        "async_get",
        lambda hass: SimpleNamespace(
            async_get_or_create=lambda **kwargs: SimpleNamespace(id="ems-hub-device")
        ),
    )


@pytest.fixture
def objects():
    entry = SimpleNamespace(
        entry_id="ems-entry",
        data={"batteries": []},
        options={"enabled": True, "feed_in": False},
    )
    runtime = SimpleNamespace(
        status="waiting",
        last_error=None,
        signal="ems_updated",
        controller=Controller(),
        _last_sent={},
        _pending={},
        _shutdown_pending={},
        _deferred_shutdown=set(),
        _start_hold=StartHold(seconds=30),
        _grid_raw_w=None,
        _grid_filtered_w=None,
    )
    changes = []

    def change_entry(config_entry, *, options):
        config_entry.options = options
        changes.append(options.copy())

    runtime.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=change_entry))
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
    diagnostics = entity.extra_state_attributes
    assert diagnostics["last_error"] is None
    assert diagnostics["control_mode"] == "proportional"
    assert diagnostics["requested_total_w"] == 0
    assert diagnostics["pending_actuator_reads"] == []
    assert diagnostics["pending_shutdown_reads"] == []


def test_negative_status_sensor_exposes_write_failure(objects):
    entry, runtime, _ = objects
    runtime.status = "write_error"
    runtime.last_error = "number.inverter: unavailable"
    entity = sensors.EMSStatus(entry, runtime)
    assert entity.native_value == "write_error"
    diagnostics = entity.extra_state_attributes
    assert diagnostics["last_error"] == "number.inverter: unavailable"
    assert diagnostics["control_mode"] == "proportional"
    assert diagnostics["pending_actuator_reads"] == []


@pytest.mark.asyncio
async def test_positive_switch_setup_creates_two_entities(objects):
    entry, runtime, _ = objects
    hass = SimpleNamespace(data={"sunenergyxt_ems": {"ems-entry": runtime}})
    created = []
    await switches.async_setup_entry(hass, entry, created.extend)
    assert len(created) == 2
    assert {entity.unique_id for entity in created} == {"ems-entry_enabled", "ems-entry_feed_in"}


@pytest.mark.asyncio
async def test_positive_sensor_setup_creates_status(objects):
    entry, runtime, _ = objects
    hass = SimpleNamespace(data={"sunenergyxt_ems": {"ems-entry": runtime}})
    created = []
    await sensors.async_setup_entry(hass, entry, created.extend)
    assert len(created) == 1 + len(sensors.PID_VALUES)
    assert created[0].unique_id == "ems-entry_status"
    assert {entity.unique_id for entity in created[1:]} == {
        f"ems-entry_{key}" for key, _ in sensors.PID_VALUES
    }
    assert all(isinstance(entity, sensors.EMSControlValue) for entity in created[1:])


@pytest.mark.asyncio
async def test_positive_switch_receives_runtime_update_signals(objects, monkeypatch):
    entry, runtime, _ = objects
    entity = switches.EMSToggle(entry, runtime, "enabled", "EMS control")
    entity.hass = SimpleNamespace()
    added = []
    writes = []

    def cancel():
        return None

    monkeypatch.setattr(switches, "async_dispatcher_connect", lambda *args: cancel)
    monkeypatch.setattr(entity, "async_on_remove", added.append)
    monkeypatch.setattr(entity, "async_write_ha_state", lambda: writes.append(1))
    await entity.async_added_to_hass()
    entity._refresh()
    assert added == [cancel]
    assert writes == [1]


@pytest.mark.asyncio
async def test_positive_status_sensor_receives_runtime_update_signals(objects, monkeypatch):
    entry, runtime, _ = objects
    entity = sensors.EMSStatus(entry, runtime)
    entity.hass = SimpleNamespace()
    added = []
    writes = []

    def cancel():
        return None

    monkeypatch.setattr(sensors, "async_dispatcher_connect", lambda *args: cancel)
    monkeypatch.setattr(entity, "async_on_remove", added.append)
    monkeypatch.setattr(entity, "async_write_ha_state", lambda: writes.append(1))
    await entity.async_added_to_hass()
    entity._refresh()
    assert added == [cancel]
    assert writes == [1]


@pytest.mark.asyncio
async def test_batteries_have_separate_visible_status_entities(objects):
    entry, runtime, _ = objects
    entry.data = {
        "batteries": [
            {"device_id": "bk1", "inverters": [{"entity_id": "number.one"}]},
            {"device_id": "bk2", "inverters": [{"entity_id": "number.two"}]},
        ]
    }
    runtime.controller = SimpleNamespace(
        enabled=True,
        state=SimpleNamespace(
            main="bk1",
            support="bk2",
            blocked=set(),
            full=set(),
            outputs={},
            ev3600_active=set(),
            ev3600_unknown=set(),
        ),
    )
    runtime._batteries = lambda: (
        SimpleNamespace(
            key="bk1",
            soc=75,
            lower_soc=10,
            upper_soc=100,
            inverters=(SimpleNamespace(key="number.one", online=True),),
            pv_input_w=None,
            ev3600_charging=False,
        ),
        SimpleNamespace(
            key="bk2",
            soc=50,
            lower_soc=10,
            upper_soc=100,
            inverters=(SimpleNamespace(key="number.two", online=True),),
            pv_input_w=None,
            ev3600_charging=False,
        ),
    )
    runtime._dc_monitored = {}
    runtime._reported_outputs = lambda batteries: {}
    runtime.entry = entry
    runtime.hass = SimpleNamespace(data={"sunenergyxt_ems": {"ems-entry": runtime}})
    added = []
    await sensors.async_setup_entry(runtime.hass, entry, added.extend)
    assert len(added) == 3 + len(sensors.PID_VALUES)
    by_id = {entity.unique_id: entity for entity in added}
    assert {"ems-entry_status", "ems-entry_battery_bk1", "ems-entry_battery_bk2"} <= set(by_id)
    main = by_id["ems-entry_battery_bk1"]
    support = by_id["ems-entry_battery_bk2"]
    assert main.native_value == "main"
    assert support.native_value == "support"
    assert main.device_info["identifiers"] != support.device_info["identifiers"]
    assert main.extra_state_attributes["soc_pct"] == 75
    assert main.extra_state_attributes["reported_limit_w"] == {}
    assert main.extra_state_attributes["measured_inverter_ac_w"] == {}


def test_unavailable_battery_has_independent_status(objects):
    entry, runtime, _ = objects
    entry.data = {"batteries": [{"device_id": "bk", "inverters": []}]}
    runtime.controller = SimpleNamespace(
        enabled=True,
        state=SimpleNamespace(main=None, support=None, blocked={"bk"}, outputs={}),
    )
    runtime._batteries = lambda: (
        SimpleNamespace(key="bk", soc=None, lower_soc=10, upper_soc=100, inverters=()),
    )
    entity = sensors.BatteryStatus(entry, runtime, "bk")
    assert entity.native_value == "blocked"
