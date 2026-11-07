"""Test exposed Home Assistant switches and status without live devices."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

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
        _last_dispatched_w={},
        _configured_inverters=lambda: set(),
        _pending={},
        _shutdown_pending={},
        _deferred_shutdown=set(),
        _start_hold=StartHold(seconds=30),
        _grid_raw_w=None,
        _grid_filtered_w=None,
        async_disable=AsyncMock(),
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

    async def record_stop():
        assert entry.options["enabled"] is True

    runtime.async_disable.side_effect = record_stop
    await entity.async_turn_off()
    runtime.async_disable.assert_awaited_once()
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
    assert diagnostics["requested_total_w"] is None
    assert diagnostics["pending_actuator_reads"] == []
    assert diagnostics["pending_shutdown_reads"] == []


def test_inverter_requested_diagnostic_uses_dispatched_not_planned_output(objects):
    entry, runtime, _ = objects
    runtime.controller.state.outputs = {"number.one": 800}
    entity = sensors.InverterLimitValue(
        entry, runtime, "bk1", "BK215", None, "number.one", "requested"
    )
    assert entity.native_value is None
    runtime._last_dispatched_w["number.one"] = 120
    # A later plan or reported output must not be presented as a new command.
    runtime.controller.state.outputs["number.one"] = 400
    assert entity.native_value == 120


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


def test_weighted_level_uses_all_batteries_and_rejects_missing_data(objects):
    entry, runtime, _ = objects
    entry.options["capacity_wh"] = {"bk1": 4300, "bk2": 2150}
    batteries = [SimpleNamespace(key="bk1", soc=80), SimpleNamespace(key="bk2", soc=20)]
    runtime._batteries = lambda: batteries
    runtime._float = lambda value: value
    entity = sensors.EMSWeightedBatteryLevel(entry, runtime)
    assert entity.available
    assert entity.native_value == 60
    batteries[1].soc = None
    assert not entity.available
    assert entity.native_value is None


@pytest.mark.asyncio
async def test_positive_switch_setup_creates_three_entities(objects):
    entry, runtime, _ = objects
    hass = SimpleNamespace(data={"sunenergyxt_ems": {"ems-entry": runtime}})
    created = []
    await switches.async_setup_entry(hass, entry, created.extend)
    assert len(created) == 3
    assert {entity.unique_id for entity in created} == {
        "ems-entry_enabled",
        "ems-entry_feed_in",
        "ems-entry_adaptive_pid",
    }


@pytest.mark.asyncio
async def test_positive_sensor_setup_creates_status(objects):
    entry, runtime, _ = objects
    hass = SimpleNamespace(data={"sunenergyxt_ems": {"ems-entry": runtime}})
    created = []
    await sensors.async_setup_entry(hass, entry, created.extend)
    assert len(created) == 3 + len(sensors.PID_VALUES)
    assert created[0].unique_id == "ems-entry_status"
    assert {entity.unique_id for entity in created[1 : 1 + len(sensors.PID_VALUES)]} == {
        f"ems-entry_{key}" for key, _ in sensors.PID_VALUES
    }
    assert all(
        isinstance(entity, sensors.EMSControlValue)
        for entity in created[1 : 1 + len(sensors.PID_VALUES)]
    )
    assert created[-1].unique_id == "ems-entry_startup_hold_remaining"


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
            grid_outage=False,
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
    assert len(added) == 4 + len(sensors.PID_VALUES) + 1 + 2 * 8
    by_id = {entity.unique_id: entity for entity in added}
    assert {"ems-entry_status", "ems-entry_battery_bk1", "ems-entry_battery_bk2"} <= set(by_id)
    main = by_id["ems-entry_battery_bk1"]
    support = by_id["ems-entry_battery_bk2"]
    assert main.native_value == "main"
    assert support.native_value == "support"
    assert main.device_info["identifiers"] != support.device_info["identifiers"]
    assert main.extra_state_attributes["soc_pct"] == 75
    assert main.extra_state_attributes["reported_limit_w"] == {}
    assert main.extra_state_attributes["reported_inverter_ac_w"] == {}
    assert by_id["ems-entry_battery_bk1_soc"].native_value == 75
    assert by_id["ems-entry_battery_bk2_soc"].native_value == 50
    assert by_id["ems-entry_battery_bk1_lower_soc"].native_value == 10
    assert not by_id["ems-entry_battery_bk1_dc_output"].available


def test_battery_diagnostics_expose_valid_values_and_reject_missing_data(objects):
    entry, runtime, _ = objects
    battery = SimpleNamespace(key="bk", soc=72.0, lower_soc=12.0, dc_output_w=234.0)
    runtime._batteries = lambda: (battery,)
    entities = {
        kind: sensors.BatteryDiagnosticValue(entry, runtime, "bk", "BK", None, kind)
        for kind in ("soc", "lower_soc", "dc_output")
    }
    assert {kind: entity.native_value for kind, entity in entities.items()} == {
        "soc": 72.0,
        "lower_soc": 12.0,
        "dc_output": 234.0,
    }
    battery.soc = None
    battery.lower_soc = 101.0
    battery.dc_output_w = None
    assert all(not entity.available for entity in entities.values())


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


def test_disabled_grid_diagnostics_do_not_display_last_running_power(objects):
    """After shutdown, last negative grid sample must not look like a live value."""
    entry, runtime, _ = objects
    runtime.controller.enabled = False
    runtime._grid_raw_w = -700.0
    runtime._grid_filtered_w = -350.0
    for key, field in (
        ("grid_raw", "raw_grid_w"),
        ("grid_effective", "effective_grid_w"),
        ("grid_error", "pid_error_w"),
    ):
        sensor = sensors.EMSControlValue(entry, runtime, key, field)
        assert sensor.native_value is None


def test_grid_loss_hides_previous_error_and_pid_contributions(objects):
    """Old controller terms are not current diagnostics after the meter is lost."""
    entry, runtime, _ = objects
    runtime.controller.enabled = True
    runtime.status = "running"
    runtime.controller.state.pid_error_w = 42.0
    runtime.controller.state.pid_p_w = 20.0
    runtime.controller.state.pid_i_w = 10.0
    runtime.controller.state.pid_d_w = 5.0
    runtime._grid_filtered_w = None
    for key, field in (
        ("grid_error", "pid_error_w"),
        ("pid_proportional", "pid_p_w"),
        ("pid_integral", "pid_i_w"),
        ("pid_derivative", "pid_d_w"),
    ):
        entity = sensors.EMSControlValue(entry, runtime, key, field)
        assert entity.native_value is None
        runtime._grid_filtered_w = 12.0
        assert entity.native_value == getattr(runtime.controller.state, field)
        runtime._grid_filtered_w = None


def test_aggregate_is_last_dispatched_limits_not_stale_controller_plan(objects):
    entry, runtime, _ = objects
    runtime.status = "running"
    runtime.controller.enabled = True
    runtime._configured_inverters = lambda: {"number.one", "number.two"}
    runtime._grid_filtered_w = 20
    runtime.controller.state.target_w = 900
    runtime._last_dispatched_w = {"number.one": 100}
    sensor = sensors.EMSControlValue(entry, runtime, "controller_target", "target_w")
    status = sensors.EMSStatus(entry, runtime)
    assert sensor.native_value is None
    assert status.extra_state_attributes["requested_total_w"] is None
    runtime._last_dispatched_w["number.two"] = 200
    assert sensor.native_value == 300
    assert status.extra_state_attributes["requested_total_w"] == 300
    # A partial emergency update must not substitute a stale 900 W plan.
    runtime.status = "reserve_safety"
    runtime._last_dispatched_w["number.one"] = 0
    assert sensor.native_value == 200
    assert status.extra_state_attributes["requested_total_w"] == 200
    del runtime._last_dispatched_w["number.two"]
    assert sensor.native_value is None


@pytest.mark.parametrize(
    "status", ["hardware_limit", "dc_unavailable", "reserve_safety", "write_error", "waiting"]
)
def test_safety_paths_do_not_expose_last_pid_cycle_as_current(objects, status):
    entry, runtime, _ = objects
    runtime.controller.enabled = True
    runtime._grid_filtered_w = 50
    runtime.controller.state.pid_error_w = 30
    runtime.status = status
    sensor = sensors.EMSControlValue(entry, runtime, "grid_error", "pid_error_w")
    assert sensor.native_value is None
    runtime.status = "running"
    assert sensor.native_value == 30
    runtime.controller.state.grid_outage = True
    assert sensor.native_value is None


@pytest.mark.parametrize("status", ["waiting_feedback", "waiting_meter"])
def test_waiting_pid_value_describes_previous_calculation(objects, status):
    entry, runtime, _ = objects
    runtime.controller.enabled = True
    runtime._grid_filtered_w = 50
    runtime.controller.state.pid_error_w = 30
    runtime.status = status
    sensor = sensors.EMSControlValue(entry, runtime, "grid_error", "pid_error_w")
    assert sensor.native_value == 30
    assert sensor.extra_state_attributes == {
        "value_semantics": "last_completed_controller_calculation",
        "controller_phase": status,
    }
