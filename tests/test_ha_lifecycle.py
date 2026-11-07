"""Black-box Home Assistant lifecycle test for the installed integration.

Unlike the focused controller unit tests, this exercises HA's own config entry,
entity platform, device registry and entity registry. It must run as a required
CI job with pytest-homeassistant-custom-component; no skip-on-missing-plugin.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util.file import WriteError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.sunenergyxt_ems import runtime as runtime_module
from custom_components.sunenergyxt_ems.const import DOMAIN
from custom_components.sunenergyxt_ems.runtime import EMSRuntime


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_weighted_battery_sensor_tracks_real_ha_states(hass: HomeAssistant, freezer) -> None:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "batteries": [
                {
                    "device_id": "bk1",
                    "soc": "sensor.soc1",
                    "lower": 10,
                    "upper": 100,
                    "inverters": [{"entity_id": "number.inv1", "rated_w": 800}],
                },
                {
                    "device_id": "bk2",
                    "soc": "sensor.soc2",
                    "lower": 10,
                    "upper": 100,
                    "inverters": [{"entity_id": "number.inv2", "rated_w": 800}],
                },
            ],
        },
        options={"enabled": False, "capacity_wh": {"bk1": 4300, "bk2": 2150}},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.soc1", "80", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.soc2", "20", {"unit_of_measurement": "%"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_weighted_battery_level"
    )
    assert entity_id is not None
    runtime = hass.data[DOMAIN][entry.entry_id]
    assert float(hass.states.get(entity_id).state) == 60
    hass.states.async_set("sensor.soc2", "50", {"unit_of_measurement": "%"})
    await runtime.async_update()
    await hass.async_block_till_done()
    assert float(hass.states.get(entity_id).state) == 70
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "capacity_wh": {"bk1": 2150, "bk2": 2150}}
    )
    await hass.async_block_till_done()
    assert float(hass.states.get(entity_id).state) == 65
    hass.states.async_set("sensor.soc2", "unavailable", {"unit_of_measurement": "%"})
    await runtime.async_update()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"
    hass.states.async_set("sensor.soc2", "50", {"unit_of_measurement": "%"})
    freezer.tick(46)
    await runtime.async_update()
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "unavailable"


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_setup_registers_real_integration_devices_and_controls(
    hass: HomeAssistant,
) -> None:
    """A loaded entry must create its devices and usable entities in real HA."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="SunEnergyXT EMS",
        data={
            "grid": "sensor.grid",
            "invert_grid": False,
            "batteries": [
                {
                    "device_id": "bk215-1",
                    "label": "BK215 1",
                    "soc": "sensor.soc",
                    "lower": 10,
                    "upper": 90,
                    "inverters": [
                        {"entity_id": "number.dummy_inverter", "rated_w": 800},
                    ],
                },
            ],
        },
        # Do not send any live commands in the integration lifecycle test.
        options={"enabled": False, "feed_in": False, "grid_target_w": 20},
    )
    entry.add_to_hass(hass)

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
    assert len(devices) == 2, "Expected EMS hub and one BK215 child device"
    assert any(device.name == "SunEnergyXT EMS" for device in devices)
    assert any("BK215" in (device.name or "") for device in devices)
    parent = next(device for device in devices if device.name == "SunEnergyXT EMS")
    child = next(device for device in devices if "BK215" in (device.name or ""))
    assert child.via_device_id == parent.id

    entities = er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
    unique_ids = {entity.unique_id for entity in entities}
    assert unique_ids == {
        f"{entry.entry_id}_status",
        f"{entry.entry_id}_weighted_battery_level",
        f"{entry.entry_id}_battery_bk215-1",
        f"{entry.entry_id}_enabled",
        f"{entry.entry_id}_feed_in",
        f"{entry.entry_id}_adaptive_pid",
        f"{entry.entry_id}_startup_hold_remaining",
        f"{entry.entry_id}_battery_bk215-1_ev3600_state",
        f"{entry.entry_id}_battery_bk215-1_bypass_state",
        f"{entry.entry_id}_inverter_number.dummy_inverter_requested",
        f"{entry.entry_id}_inverter_number.dummy_inverter_reported",
        f"{entry.entry_id}_inverter_number.dummy_inverter_measured_ac",
    } | {
        f"{entry.entry_id}_{key}"
        for key in (
            "grid_raw",
            "grid_effective",
            "grid_error",
            "pid_proportional",
            "pid_integral",
            "pid_derivative",
            "controller_target",
        )
    } | {
        f"{entry.entry_id}_{key}"
        for key in (
            "start_hold_seconds",
            "pid_kp_min",
            "pid_kp_max",
            "pid_kp_export",
            "pid_ki",
            "pid_kd",
        )
    }
    assert all(hass.states.get(entity.entity_id) is not None for entity in entities)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_inverter_diagnostics_dispatch_and_reload_in_real_ha(hass: HomeAssistant) -> None:
    """The dispatched command, HA readback and independent AC source remain distinct."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "batteries": [
                {
                    "device_id": "bk1",
                    "soc": "sensor.soc",
                    "lower": 10,
                    "upper": 100,
                    "inverters": [
                        {"entity_id": "number.inv1", "rated_w": 2000},
                        {"entity_id": "number.inv2", "rated_w": 800},
                    ],
                }
            ],
        },
        options={"enabled": False, "ac_power": {"number.inv1": "sensor.ac1"}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_ids = {
        (inverter, kind): registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_inverter_{inverter}_{kind}"
        )
        for inverter in ("number.inv1", "number.inv2")
        for kind in ("requested", "reported", "measured_ac")
    }
    assert all(entity_ids.values())
    hass.states.async_set(
        "number.inv1", "10", {"unit_of_measurement": "%", "min": 0, "max": 100, "step": 0.1}
    )
    hass.states.async_set(
        "number.inv2", "100", {"unit_of_measurement": "W", "min": 0, "max": 800, "step": 1}
    )
    hass.states.async_set("sensor.ac1", "120", {"unit_of_measurement": "W"})
    runtime = hass.data[DOMAIN][entry.entry_id]
    commands = []

    async def set_value(call):
        commands.append(dict(call.data))

    hass.services.async_register("number", "set_value", set_value)
    await runtime._apply({"number.inv1": 400}, now=100, emergency=True, force=True)
    assert len(commands) == 1
    assert commands[0]["entity_id"] == "number.inv1"
    assert commands[0]["value"] == 20
    runtime._publish()
    await hass.async_block_till_done()
    assert float(hass.states.get(entity_ids[("number.inv1", "requested")]).state) == 400
    assert float(hass.states.get(entity_ids[("number.inv1", "reported")]).state) == 200
    assert float(hass.states.get(entity_ids[("number.inv1", "measured_ac")]).state) == 120
    assert hass.states.get(entity_ids[("number.inv2", "requested")]).state == "unknown"
    assert float(hass.states.get(entity_ids[("number.inv2", "reported")]).state) == 100

    hass.states.async_set("number.inv1", "unavailable", {"unit_of_measurement": "%"})
    hass.states.async_set("number.inv2", "unavailable", {"unit_of_measurement": "W"})
    runtime._publish()
    await hass.async_block_till_done()
    assert hass.states.get(entity_ids[("number.inv1", "measured_ac")]).state == "unavailable"
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert len(commands) == 1
    assert hass.states.get(entity_ids[("number.inv1", "requested")]).state == "unknown"
    assert {
        (inverter, kind): registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_inverter_{inverter}_{kind}"
        )
        for inverter, kind in entity_ids
    } == entity_ids


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_write", [False, True])
async def test_offline_shutdown_survives_runtime_reload_in_real_ha(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, failed_write: bool
) -> None:
    """Real HA storage must retain a failed safe-off until a fresh readback."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "batteries": [
                {
                    "device_id": "bk215-1",
                    "soc": "sensor.soc",
                    "lower": 10,
                    "upper": 100,
                    "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
                }
            ],
        },
        options={"enabled": False},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("number.m80", "unavailable", {"unit_of_measurement": "%"})
    commands = []

    async def set_value(call):
        commands.append(dict(call.data))

    hass.services.async_register("number", "set_value", set_value)
    first = EMSRuntime(hass, entry)
    if failed_write:
        monkeypatch.setattr(
            first._shutdown_store, "_async_write_data", AsyncMock(side_effect=WriteError("disk"))
        )
    await first.async_disable()
    assert not commands
    if failed_write:
        assert "did not confirm" in (first.last_error or "")
        return
    recovered = EMSRuntime(hass, entry)
    await recovered._load_shutdown_obligations()
    assert recovered._shutdown_obligations == {"number.m80"}
    hass.states.async_set(
        "number.m80", "100", {"unit_of_measurement": "%", "min": 0, "max": 100, "step": 0.1}
    )
    recovered._running = True
    await recovered.async_update()
    assert commands == [{"entity_id": "number.m80", "value": 0}]
    assert (await recovered._shutdown_store.async_load())["pending"] == ["number.m80"]
    hass.states.async_set(
        "number.m80", "0", {"unit_of_measurement": "%", "min": 0, "max": 100, "step": 0.1}
    )
    await recovered.async_update()
    assert (await recovered._shutdown_store.async_load())["pending"] == []


@pytest.mark.asyncio
async def test_real_ha_captures_sub_five_second_solarman_link_flap(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Replay 4.83 s M80 outage/reconnect entirely between 5 s poll ticks."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "batteries": [
                {
                    "device_id": "bk215-2",
                    "soc": "sensor.soc",
                    "lower": 10,
                    "upper": 100,
                    "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
                }
            ],
        },
        options={"enabled": False},
    )
    entry.add_to_hass(hass)
    monkeypatch.setattr(
        runtime_module,
        "registered_connection",
        lambda _hass, key: "binary_sensor.m80_connection" if key == "number.m80" else None,
    )
    attrs = {"unit_of_measurement": "%", "min": 0, "max": 100, "step": 0.1}
    hass.states.async_set("number.m80", "0", attrs)
    hass.states.async_set("binary_sensor.m80_connection", "on")
    commands = []

    async def set_value(call):
        commands.append(dict(call.data))

    hass.services.async_register("number", "set_value", set_value)
    runtime = EMSRuntime(hass, entry)
    await runtime._shutdown_store.async_save({"pending": []})
    await runtime.async_start()
    assert runtime.status == "disabled"
    assert not commands

    # These two link events occur faster than the normal polling interval.
    hass.states.async_set("binary_sensor.m80_connection", "off")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.m80_connection", "on")
    hass.states.async_set("number.m80", "100", attrs)
    await hass.async_block_till_done()

    assert any(
        command["entity_id"] == "number.m80" and command["value"] == 0 for command in commands
    ), "A 4.83 s offline/on cycle must reissue zero before the next scheduled poll"
    assert "number.m80" in runtime._shutdown_obligations
    await runtime.async_stop()


@pytest.mark.usefixtures("enable_custom_integrations")
async def test_ha_diagnostics_exposes_selected_mapping_without_secrets(
    hass: HomeAssistant, hass_client
) -> None:
    """Only explicitly needed EMS fields reach the actual HA download API."""
    battery = {
        "device_id": "device-one",
        "soc": "sensor.battery",
        "lower": "number.minimum_soc",
        "upper": 95,
        "charging": "sensor.charging",
        "dc_output": "sensor.dc_power",
        "inverters": [{"entity_id": "number.m200", "rated_w": 2000, "token": "inv-secret"}],
        "password": "battery-secret",
    }
    second_battery = {
        "device_id": "device-two",
        "soc": "sensor.battery_two",
        "lower": 10,
        "inverters": [{"entity_id": "number.m80", "rated_w": 800}],
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "invert_grid": True,
            "batteries": [battery, second_battery],
            "token": "secret",
        },
        options={"enabled": False, "password": "options-secret"},
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "diagnostics", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()
    response = await client.get(f"/api/diagnostics/config_entry/{entry.entry_id}")
    assert response.status == 200
    body = await response.json()
    assert body["data"]["configuration"] == {
        "grid": "sensor.grid",
        "invert_grid": True,
        "batteries": [
            {
                "device_id": "device-one",
                "soc": "sensor.battery",
                "lower": "number.minimum_soc",
                "upper": 95,
                "charging": "sensor.charging",
                "dc_output": "sensor.dc_power",
                "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
            },
            second_battery,
        ],
    }
    assert "secret" not in json.dumps(body["data"])


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("lower", ["number.manually_selected_reserve", 10])
async def test_reconfigure_retains_existing_battery_mappings_when_discovery_changes(
    hass: HomeAssistant,
    lower: str | int,
) -> None:
    """Selecting the same battery must not replace its explicitly chosen sources."""
    original = {
        "device_id": "bk1",
        "soc": "sensor.manually_selected_soc",
        "lower": lower,
        "upper": "number.manually_selected_upper",
        "charging": "sensor.manually_selected_charging",
        "dc_output": "sensor.manually_selected_dc",
        "inverters": [{"entity_id": "number.inv1", "rated_w": 2000}],
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="household",
        data={
            "batteries": [
                original,
                {
                    "device_id": "bk2",
                    "soc": "sensor.other_soc",
                    "lower": 10,
                    "upper": 95,
                    "inverters": [{"entity_id": "number.inv2", "rated_w": 800}],
                },
            ],
            "grid": "sensor.grid",
            "invert_grid": False,
        },
        options={"enabled": False},
    )
    entry.add_to_hass(hass)
    reg = er.async_get(hass)
    reg.async_get_or_create("number", "test", "inv1", suggested_object_id="inv1")
    hass.states.async_set("number.inv1", "0", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.grid", "100", {"unit_of_measurement": "W"})
    with (
        patch(
            "custom_components.sunenergyxt_ems.config_flow.battery_suggestions",
            return_value={
                "bk1": {
                    "soc": "sensor.discovered_soc",
                    "lower": "number.discovered_reserve",
                    "upper": "number.discovered_upper",
                    "charging": "sensor.discovered_charging",
                    "dc_output": "sensor.discovered_dc",
                }
            },
        ),
        patch(
            "custom_components.sunenergyxt_ems.config_flow.inverter_device_suggestions",
            return_value={"number.inv1": "M200"},
        ),
        patch("custom_components.sunenergyxt_ems.config_flow.device_rated_w", return_value=2000),
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        assert flow["step_id"] == "reconfigure"
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"batteries": ["bk1"], "correct_values": False}
        )
        assert flow["step_id"] == "inverters"
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"inverters_1": ["number.inv1"]}
        )
        assert flow["step_id"] == "grid"
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"grid": "sensor.grid", "invert_grid": False, "ack": True}
        )
        assert flow["reason"] == "reconfigure_successful"
    restored = entry.data["batteries"][0]
    assert len(entry.data["batteries"]) == 1
    for key in ("soc", "lower", "upper", "charging", "dc_output"):
        assert restored[key] == original[key]
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    ("lower", "upper"),
    [(0, 95), (10, 95), ("number.reserve", "number.upper")],
)
async def test_correction_form_preserves_limit_value_types(hass: HomeAssistant, lower, upper):
    """Real reconfigure form retains numeric zero and numbers or entity IDs."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="household",
        data={
            "grid": "sensor.grid",
            "batteries": [
                {
                    "device_id": "bk1",
                    "soc": "sensor.soc",
                    "lower": lower,
                    "upper": upper,
                    "inverters": [{"entity_id": "number.inv", "rated_w": 2000}],
                }
            ],
        },
        options={"enabled": False},
    )
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create("number", "test", "inv", suggested_object_id="inv")
    hass.states.async_set("number.inv", "0", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.grid", "100", {"unit_of_measurement": "W"})
    with (
        patch(
            "custom_components.sunenergyxt_ems.config_flow.battery_suggestions",
            return_value={"bk1": {"soc": "sensor.soc", "lower": lower, "upper": upper}},
        ),
        patch(
            "custom_components.sunenergyxt_ems.config_flow.inverter_device_suggestions",
            return_value={"number.inv": "M200"},
        ),
        patch("custom_components.sunenergyxt_ems.config_flow.device_rated_w", return_value=2000),
    ):
        flow = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_RECONFIGURE, "entry_id": entry.entry_id},
        )
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"batteries": ["bk1"], "correct_values": True}
        )
        assert flow["step_id"] == "correct"
        defaults = {
            str(key): key.default()
            for key in flow["data_schema"].schema
            if callable(getattr(key, "default", None))
        }
        assert defaults["lower_1"] == lower
        assert defaults["upper_1"] == upper
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"soc_1": "sensor.soc", "lower_1": lower, "upper_1": upper}
        )
        assert flow["step_id"] == "inverters"
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"inverters_1": ["number.inv"]}
        )
        assert flow["step_id"] == "grid"
        flow = await hass.config_entries.flow.async_configure(
            flow["flow_id"], {"grid": "sensor.grid", "invert_grid": False, "ack": True}
        )
        assert flow["reason"] == "reconfigure_successful"
    assert entry.data["batteries"][0]["lower"] == lower
    assert entry.data["batteries"][0]["upper"] == upper
    await hass.async_block_till_done()
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_grid_loss_and_recovery_do_not_expose_old_error(hass: HomeAssistant, freezer) -> None:
    """Recovering one meter report must not present an old PID sample as current."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "batteries": [
                {
                    "device_id": "bk1",
                    "soc": "sensor.soc",
                    "lower": 10,
                    "upper": 100,
                    "inverters": [],
                }
            ],
        },
        options={"enabled": True, "grid_target_w": 20},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.grid", "100", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.soc", "80", {"unit_of_measurement": "%"})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN][entry.entry_id]
    # Simulate a previously running controller without creating actuator writes.
    runtime._seen_valid = True
    await runtime.async_update()
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_grid_error"
    )
    assert entity_id is not None
    assert float(hass.states.get(entity_id).state) == 80.0

    runtime.controller.state.last_grid_ok = None
    hass.states.async_set("sensor.grid", "unavailable", {"unit_of_measurement": "W"})
    await runtime.async_update()
    await hass.async_block_till_done()
    assert runtime.controller.state.grid_outage
    assert hass.states.get(entity_id).state == "unknown"
    for reading in (120, 121):
        freezer.tick(1)
        hass.states.async_set("sensor.grid", str(reading), {"unit_of_measurement": "W"})
        await runtime.async_update()
        await hass.async_block_till_done()
        assert runtime.controller.state.grid_outage
        assert hass.states.get(entity_id).state == "unknown"
    freezer.tick(1)
    hass.states.async_set("sensor.grid", "122", {"unit_of_measurement": "W"})
    await runtime.async_update()
    await hass.async_block_till_done()
    assert not runtime.controller.state.grid_outage
    assert float(hass.states.get(entity_id).state) == 101.0
