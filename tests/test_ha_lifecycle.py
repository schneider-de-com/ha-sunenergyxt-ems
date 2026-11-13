"""Black-box Home Assistant lifecycle test for the installed integration.

Unlike the focused controller unit tests, this exercises HA's own config entry,
entity platform, device registry and entity registry. It must run as a required
CI job with pytest-homeassistant-custom-component; no skip-on-missing-plugin.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store as HAStore
from homeassistant.helpers.translation import async_get_translations
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.setup import async_setup_component
from homeassistant.util.file import WriteError
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

from custom_components.sunenergyxt_ems import runtime as runtime_module
from custom_components.sunenergyxt_ems.const import DOMAIN
from custom_components.sunenergyxt_ems.observer import DemandObserver, HouseholdObservation
from custom_components.sunenergyxt_ems.runtime import EMSRuntime

# Preserve Core's decoder before test fixtures replace disk reads with dicts.
_CORE_STORE_ASYNC_LOAD = HAStore._async_load


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize("owner_ready", [True, False])
@pytest.mark.asyncio
async def test_forecast_solar_real_coordinator_receipts_service_and_unload(
    hass, freezer, owner_ready
):
    """Core's coordinator and response service cross the actual EMS lifecycle."""
    from custom_components.sunenergyxt_ems.diagnostics import async_get_config_entry_diagnostics
    from custom_components.sunenergyxt_ems.forecast import ForecastError

    at = datetime(2026, 11, 13, 12, tzinfo=UTC)
    freezer.move_to(at)
    owner = MockConfigEntry(
        domain="forecast_solar",
        state=ConfigEntryState.LOADED if owner_ready else ConfigEntryState.NOT_LOADED,
    )
    owner.add_to_hass(hass)
    coordinator = DataUpdateCoordinator(
        hass, logging.getLogger(__name__), name="source contract", config_entry=owner
    )
    coordinator.data = SimpleNamespace(timezone="UTC")
    owner.runtime_data = coordinator
    registry = er.async_get(hass)
    selected = {}
    for role, value in (("today", "0.3"), ("tomorrow", "0")):
        sensor = registry.async_get_or_create(
            "sensor",
            "forecast_solar",
            f"{owner.entry_id}_energy_production_{role}",
            config_entry=owner,
            suggested_object_id=f"forecast_{role}",
        )
        selected[role] = sensor.entity_id
        if owner_ready:
            hass.states.async_set(sensor.entity_id, value, {"unit_of_measurement": "kWh"})
    times = [at - timedelta(hours=1), at, at + timedelta(hours=1), at + timedelta(days=1)]
    response = {
        "watts": {t.isoformat(): 9000 for t in times},
        "wh_period": {t.isoformat(): wh for t, wh in zip(times[:-1], (100, 200, 0), strict=True)},
    }
    calls = []

    async def cached(call):
        calls.append(dict(call.data))
        return response

    hass.services.async_register(
        "forecast_solar", "get_forecast", cached, supports_response=SupportsResponse.ONLY
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"grid": "sensor.grid", "batteries": []},
        options={"enabled": False, "planning_enabled": True, "pv_forecast": selected},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    runtime = hass.data[DOMAIN][entry.entry_id]
    if not owner_ready:
        assert runtime.horizon.forecast_solar_sources == {}
        owner._async_set_state(hass, ConfigEntryState.LOADED, None)
        for role, value in (("today", "0.3"), ("tomorrow", "0")):
            hass.states.async_set(selected[role], value, {"unit_of_measurement": "kWh"})
        # Advance both the real HA timer and the synthetic cache horizon.
        at += timedelta(seconds=5)
        response = {
            key: {
                (datetime.fromisoformat(stamp) + timedelta(seconds=5)).isoformat(): value
                for stamp, value in series.items()
            }
            for key, series in response.items()
        }
        freezer.move_to(at)
        async_fire_time_changed(hass, at)
        await hass.async_block_till_done()
        assert runtime.controller.enabled is False
    source = runtime.horizon.forecast_solar_sources[owner.entry_id]
    planning_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_planning_status"
    )
    status = hass.states.get(planning_id)
    assert status.state == "ems_disabled"
    assert (
        status.attributes["forecast_solar_sources"][0]["reason"] == "forecast_solar_receipt_unknown"
    )
    assert status.attributes["complete_household_intervals"] == 0
    assert status.attributes["household_measurement_quality"] == "grid_unavailable"
    assert status.attributes["capture_persistence_ok"] is True
    assert calls == []
    with pytest.raises(ForecastError, match="receipt_unknown"):
        source.forecast(
            now=at,
            required_until=at + timedelta(hours=2),
            source_entities=(selected["today"], selected["tomorrow"]),
        )
    coordinator.async_set_updated_data(SimpleNamespace(timezone="UTC"))
    await hass.async_block_till_done()
    point = source.forecast(
        now=at,
        required_until=at + timedelta(hours=2),
        source_entities=(selected["today"], selected["tomorrow"]),
    )
    assert sum(slot.energy_wh for slot in point.expected) == 200
    assert point.provider_issued_at is None and point.lower is None and point.upper is None
    status = hass.states.get(planning_id)
    assert status.state == "ems_disabled"
    exposed = status.attributes["forecast_solar_sources"][0]
    assert exposed["received_at"] == at.isoformat()
    assert exposed["reason"] == "cached_point_forecast"
    assert exposed["uncertainty"] == "not_supplied"
    assert "watts" not in exposed and "api_key" not in exposed
    assert calls == [{"config_entry": owner.entry_id, "resolution": "raw"}]
    # Even a current nominal forecast cannot masquerade as planner confidence.
    with pytest.raises(ForecastError, match="uncertainty_not_supplied"):
        runtime.horizon._solar(selected, at, at + timedelta(hours=2))
    assert runtime.controller.planning_order == ()
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    description = diagnostics["forecast_sources"]["forecast_solar"][0]
    assert description["received_at"] == at.isoformat()
    assert description["uncertainty"] == "not_supplied"
    assert "watts" not in description and "api_key" not in description
    freezer.move_to(at + timedelta(hours=2, seconds=1))
    runtime._publish()
    await hass.async_block_till_done()
    assert hass.states.get(planning_id).attributes["forecast_solar_sources"][0]["reason"] == (
        "forecast_solar_receipt_stale"
    )
    assert len(calls) == 1  # Rendering status never refreshes a provider or its cache service.
    assert await hass.config_entries.async_unload(entry.entry_id)
    coordinator.async_set_updated_data(SimpleNamespace(timezone="UTC"))
    await hass.async_block_till_done()
    assert len(calls) == 1
    # The owner fixture supplies a cache contract, not a fully set-up cloud integration.
    owner._async_set_state(hass, ConfigEntryState.NOT_LOADED, None)


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_options_select_forecast_solar_pair_without_reusing_solcast_timestamp(hass):
    """A provider switch retains control settings and drops the inapplicable timestamp."""
    provider = MockConfigEntry(domain="forecast_solar")
    provider.add_to_hass(hass)
    registry = er.async_get(hass)
    selected = {}
    for role, key in (
        ("today", "energy_production_today"),
        ("tomorrow", "energy_production_tomorrow"),
    ):
        source = registry.async_get_or_create(
            "sensor",
            "forecast_solar",
            f"{provider.entry_id}_{key}",
            config_entry=provider,
            suggested_object_id=f"new_{role}",
        )
        selected[role] = source.entity_id
        hass.states.async_set(source.entity_id, "1.2", {"unit_of_measurement": "kWh"})
    original = {
        "enabled": False,
        "feed_in": True,
        "grid_target_w": 20,
        "pid_ki": 0.07,
        "pv_forecast": {
            "today": "sensor.old_today",
            "tomorrow": "sensor.old_tomorrow",
            "updated": "sensor.old_api_success",
        },
    }
    entry = MockConfigEntry(domain=DOMAIN, data={"batteries": []}, options=original)
    entry.add_to_hass(hass)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"],
        {"pv_forecast_today": selected["today"], "pv_forecast_tomorrow": selected["tomorrow"]},
    )
    assert result["type"] == "create_entry"
    assert entry.options["pv_forecast"] == selected
    for key in ("enabled", "feed_in", "grid_target_w", "pid_ki"):
        assert entry.options[key] == original[key]


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.parametrize(
    "fault", ["mixed_entry", "wrong_role", "wrong_unit", "disabled", "partial"]
)
@pytest.mark.asyncio
async def test_options_reject_invalid_forecast_solar_binding_without_mutation(hass, fault):
    provider, other = (
        MockConfigEntry(domain="forecast_solar"),
        MockConfigEntry(domain="forecast_solar"),
    )
    provider.add_to_hass(hass)
    other.add_to_hass(hass)
    registry = er.async_get(hass)
    selected = {}
    for role in ("today", "tomorrow"):
        owner = other if fault == "mixed_entry" and role == "tomorrow" else provider
        key = (
            "energy_current_hour"
            if fault == "wrong_role" and role == "tomorrow"
            else f"energy_production_{role}"
        )
        source = registry.async_get_or_create(
            "sensor",
            "forecast_solar",
            f"{owner.entry_id}_{key}",
            config_entry=owner,
            suggested_object_id=f"candidate_{role}",
        )
        selected[role] = source.entity_id
        unit = "W" if fault == "wrong_unit" and role == "tomorrow" else "kWh"
        hass.states.async_set(source.entity_id, "1.2", {"unit_of_measurement": unit})
        if fault == "disabled" and role == "tomorrow":
            registry.async_update_entity(
                source.entity_id, disabled_by=er.RegistryEntryDisabler.USER
            )
    original = {"enabled": False, "feed_in": True, "grid_target_w": 20, "pid_ki": 0.07}
    entry = MockConfigEntry(domain=DOMAIN, data={"batteries": []}, options=original)
    entry.add_to_hass(hass)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    data = {"pv_forecast_today": selected["today"]}
    if fault != "partial":
        data["pv_forecast_tomorrow"] = selected["tomorrow"]
    result = await hass.config_entries.options.async_configure(form["flow_id"], data)
    assert result["type"] == "form"
    assert result["errors"] == {"base": "incomplete_forecast_mapping"}
    assert entry.options == original


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_options_reject_energy_as_pv_power_and_allow_explicit_removal(hass):
    """A bad optional mapping can be rejected, corrected and removed through HA."""
    original = {
        "enabled": False,
        "feed_in": False,
        "grid_target_w": 20,
        "pid_ki": 0.07,
        "capacity_wh": {"bk1": 4300},
        "pv_input": {"bk1": "sensor.monthly_energy"},
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"batteries": [{"device_id": "bk1", "lower": 10, "upper": 95}]},
        options=original,
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.monthly_energy", "0.3328", {"unit_of_measurement": "kWh"})
    hass.states.async_set("sensor.pv_power", "unavailable", {"unit_of_measurement": "kW"})
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"pv_input_1": "sensor.monthly_energy"}
    )
    assert result["type"] == "form"
    assert result["errors"] == {"pv_input_1": "invalid_pv_power_source"}
    assert entry.options == original
    for source, attributes in (
        ("sensor.soc", {"unit_of_measurement": "%"}),
        ("sensor.no_unit", {}),
        ("sensor.missing", None),
    ):
        if attributes is not None:
            hass.states.async_set(source, "10", attributes)
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], {"pv_input_1": source}
        )
        assert result["errors"] == {"pv_input_1": "invalid_pv_power_source"}
        assert entry.options == original
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"pv_input_1": "sensor.pv_power"}
    )
    assert result["type"] == "create_entry"
    assert entry.options["pv_input"] == {"bk1": "sensor.pv_power"}
    # An offline but correctly typed source remains configurable; runtime decides freshness.
    for unit in ("W", "kW"):
        hass.states.async_set("sensor.pv_power", "100", {"unit_of_measurement": unit})
        form = await hass.config_entries.options.async_init(entry.entry_id)
        result = await hass.config_entries.options.async_configure(
            form["flow_id"], {"pv_input_1": "sensor.pv_power"}
        )
        assert result["type"] == "create_entry"
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"clear_pv_input_1": True}
    )
    assert result["type"] == "create_entry"
    assert entry.options["pv_input"] == {}
    for key in ("enabled", "feed_in", "grid_target_w", "pid_ki", "capacity_wh"):
        assert entry.options[key] == original[key]
    form = await hass.config_entries.options.async_init(entry.entry_id)
    assert "clear_pv_input_1" not in form["data_schema"].schema
    # Removal must also work directly on the pre-existing invalid mapping.
    hass.config_entries.options.async_abort(form["flow_id"])
    hass.config_entries.async_update_entry(entry, options=original)
    form = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        form["flow_id"], {"clear_pv_input_1": True}
    )
    assert result["type"] == "create_entry"
    assert entry.options["pv_input"] == {}
    for language in ("en", "de"):
        translated = await async_get_translations(hass, language, "options", {DOMAIN})
        assert any("invalid_pv_power_source" in key for key in translated)


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_translated_enum_statuses_in_real_ha(hass: HomeAssistant) -> None:
    """HA receives all supported status states and both languages can display them."""
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
                    "inverters": [{"entity_id": "number.inv", "rated_w": 800}],
                }
            ],
        },
        options={"enabled": False},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    status_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_status")
    battery_id = registry.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_battery_bk1")
    assert status_id is not None and battery_id is not None
    runtime = hass.data[DOMAIN][entry.entry_id]
    status_options = hass.states.get(status_id).attributes["options"]
    battery_options = hass.states.get(battery_id).attributes["options"]
    assert set(status_options) == {
        "waiting",
        "running",
        "waiting_feedback",
        "waiting_meter",
        "disabled",
        "shutdown_pending",
        "write_error",
        "hardware_limit",
        "dc_unavailable",
        "reserve_safety",
        "grid_unavailable",
        "switching_main",
    }
    assert set(battery_options) == {
        "disabled",
        "blocked",
        "ev3600_charging",
        "ev3600_unavailable",
        "unavailable",
        "main",
        "support",
        "standby",
    }
    for value in status_options:
        runtime.status = value
        runtime._publish()
        await hass.async_block_till_done()
        state = hass.states.get(status_id)
        assert state.state == value
        assert state.attributes["device_class"] == "enum"
        assert state.attributes["options"] == status_options
    state = hass.states.get(battery_id)
    assert state.state == "disabled"
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == battery_options
    for language in ("en", "de"):
        translations = await async_get_translations(hass, language, "entity", {DOMAIN})
        for key, options in (
            ("status", status_options),
            ("battery_status", battery_options),
        ):
            for option in options:
                assert translations[f"component.{DOMAIN}.entity.sensor.{key}.state.{option}"]


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
                    "dc_output": "sensor.battery_dc",
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
    hass.states.async_set("sensor.soc", "78", {"unit_of_measurement": "%"})
    hass.states.async_set("sensor.battery_dc", "120", {"unit_of_measurement": "W"})

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
        f"{entry.entry_id}_planning_status",
        f"{entry.entry_id}_weighted_battery_level",
        f"{entry.entry_id}_battery_bk215-1",
        f"{entry.entry_id}_battery_bk215-1_soc",
        f"{entry.entry_id}_battery_bk215-1_lower_soc",
        f"{entry.entry_id}_battery_bk215-1_dc_output",
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

    registry = er.async_get(hass)

    def state_for(kind: str) -> str:
        entity_id = registry.async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_battery_bk215-1_{kind}"
        )
        assert entity_id is not None
        return hass.states.get(entity_id).state

    assert float(state_for("soc")) == 78
    assert float(state_for("lower_soc")) == 10
    assert float(state_for("dc_output")) == 120
    hass.states.async_set("sensor.soc", "unavailable", {"unit_of_measurement": "%"})
    hass.data[DOMAIN][entry.entry_id]._publish()
    await hass.async_block_till_done()
    assert state_for("soc") == "unavailable"

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.NOT_LOADED
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert state_for("soc") == "unavailable"
    assert float(state_for("lower_soc")) == 10
    assert len(er.async_entries_for_config_entry(registry, entry.entry_id)) == len(unique_ids)


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
    assert hass.states.get(entity_ids[("number.inv1", "reported")]).state == "unavailable"
    assert hass.states.get(entity_ids[("number.inv2", "reported")]).state == "unavailable"
    assert float(hass.states.get(entity_ids[("number.inv1", "requested")]).state) == 400
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
async def test_diagnostics_distinguishes_cached_reports_from_device_samples(
    hass: HomeAssistant, hass_client, freezer
) -> None:
    """The download explains constant, unavailable and missing AC sources without polling."""
    attributes = {"unit_of_measurement": "W", "token": "source-secret"}
    hass.states.async_set("sensor.constant_ac", "0", attributes)
    original_change = hass.states.get("sensor.constant_ac").last_changed
    freezer.tick(300)
    hass.states.async_set("sensor.constant_ac", "0", attributes)
    hass.states.async_set("sensor.offline_ac", "unavailable", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.invalid_ac", "raw-state-secret", {"unit_of_measurement": "kWh"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "batteries": [
                {
                    "device_id": "bk1",
                    "lower": 10,
                    "inverters": [
                        {"entity_id": f"number.inv{index}", "rated_w": 800} for index in (1, 2)
                    ],
                },
                {
                    "device_id": "bk2",
                    "lower": 10,
                    "inverters": [
                        {"entity_id": f"number.inv{index}", "rated_w": 800} for index in (3, 4)
                    ],
                },
            ],
        },
        options={
            "enabled": False,
            "ac_power": {
                "number.inv1": "sensor.constant_ac",
                "number.inv2": "sensor.offline_ac",
                "number.inv3": "sensor.invalid_ac",
                "number.inv4": "sensor.missing_ac",
            },
        },
    )
    entry.add_to_hass(hass)
    assert await async_setup_component(hass, "diagnostics", {})
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_client()
    before = dict(entry.options)
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as calls:
        response = await client.get(f"/api/diagnostics/config_entry/{entry.entry_id}")
        assert response.status == 200
        body = await response.json()
        calls.assert_not_awaited()
    sources = body["data"]["measurement_sources"]
    assert sources["semantics"] == "ha_cached_state_timestamps_not_device_samples"
    constant, offline, invalid, missing = sources["ac_sources"]
    assert constant["entity_id"] == "sensor.constant_ac"
    assert constant["state_status"] == "present"
    assert constant["last_changed"] == original_change.isoformat()
    assert constant["value_change_age_seconds"] == 300
    assert constant["report_age_seconds"] == 0
    assert constant["device_sample_at"] is None
    assert offline["state_status"] == "unavailable"
    assert offline["device_sample_at"] is None
    assert invalid["state_status"] == "present"
    assert invalid["power_unit_supported"] is False
    assert missing["state_status"] == "missing"
    assert missing["report_age_seconds"] is None
    assert "source-secret" not in json.dumps(sources)
    assert "raw-state-secret" not in json.dumps(sources)
    assert entry.options == before
    assert hass.states.get("sensor.constant_ac").last_changed == original_change

    assert await hass.config_entries.async_unload(entry.entry_id)
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as calls:
        response = await client.get(f"/api/diagnostics/config_entry/{entry.entry_id}")
        assert response.status == 200
        unloaded = await response.json()
        calls.assert_not_awaited()
    assert unloaded["data"]["measurement_sources"] == sources


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
    assert hass.states.get(entity_id).state == "unavailable"
    for reading in (120, 121):
        freezer.tick(1)
        hass.states.async_set("sensor.grid", str(reading), {"unit_of_measurement": "W"})
        await runtime.async_update()
        await hass.async_block_till_done()
        assert runtime.controller.state.grid_outage
        assert hass.states.get(entity_id).state == "unavailable"
    freezer.tick(1)
    hass.states.async_set("sensor.grid", "122", {"unit_of_measurement": "W"})
    await runtime.async_update()
    await hass.async_block_till_done()
    assert not runtime.controller.state.grid_outage
    assert float(hass.states.get(entity_id).state) == 101.0


@pytest.mark.asyncio
async def test_demand_observer_uses_real_ha_store_and_preserves_restart_gap(
    hass: HomeAssistant, freezer, hass_storage
) -> None:
    """A new observer instance reads Core Store data, not the old object's RAM."""
    from homeassistant.util import dt as dt_util

    freezer.move_to("2026-11-08T12:00:00+00:00")
    entry = MockConfigEntry(domain=DOMAIN, data={"grid": "sensor.grid", "batteries": []})

    def reading():
        return HouseholdObservation(dt_util.utcnow(), 600, "measured")

    observer = DemandObserver(hass, entry, reading)
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as services:
        await observer.async_start()
        for _ in range(60):
            freezer.tick(30)
            await observer.async_refresh(dt_util.utcnow())
        await observer.async_stop()
        key = f"{DOMAIN}.demand.{entry.entry_id}"
        assert hass_storage[key]["data"]["history"]["intervals"], (
            observer.reason,
            observer.events,
            observer.last_poll_at,
        )
        assert hass_storage[key]["data"]["history"]["intervals"][0]["energy_wh"] == 300
        freezer.tick(300)
        restarted = DemandObserver(hass, entry, reading)
        await restarted.async_start()
        assert restarted.persistence_ok
        assert restarted.history.intervals[0].energy_wh == 300
        assert any(event["reason"] == "observer_gap" for event in restarted.events)
        await restarted.async_stop()
        services.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_actual_entry_captures_partial_power_channels_and_restores_evidence(
    hass: HomeAssistant, freezer, hass_storage
) -> None:
    from homeassistant.util import dt as dt_util

    freezer.move_to("2026-11-12T12:00:00+00:00")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "batteries": [
                {
                    "device_id": "bk",
                    "soc": "sensor.soc",
                    "lower": 10,
                    "upper": 100,
                    "dc_output": "sensor.dc",
                    "charging": "sensor.pv",
                    "inverters": [],
                }
            ],
        },
        options={"enabled": False},
    )
    entry.add_to_hass(hass)
    hass.states.async_set("sensor.grid", "3600", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.dc", "720", {"unit_of_measurement": "W"})
    hass.states.async_set("sensor.pv", "unavailable", {"unit_of_measurement": "W"})
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as services:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        observer = hass.data[DOMAIN][entry.entry_id].observer
        freezer.tick(5)
        await observer.async_refresh(dt_util.utcnow())
        assert await hass.config_entries.async_unload(entry.entry_id)
        key = f"{DOMAIN}.demand.{entry.entry_id}"
        channels = hass_storage[key]["data"]["operating_history"]["rows"][0]["channels"]
        assert channels["grid:sensor.grid"]["positive_wh"] == 5
        assert channels["dc_output:bk:sensor.dc"]["positive_wh"] == 1
        assert "pv:bk:sensor.pv" not in channels
        freezer.tick(120)
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        restored = hass.data[DOMAIN][entry.entry_id].observer.operating
        assert sum(row["states"].get("observer_gap", 0) for row in restored.rows) == 120
        assert restored.rows[0]["channels"]["grid:sensor.grid"]["positive_wh"] == 5
        assert await hass.config_entries.async_unload(entry.entry_id)
        services.assert_not_called()


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_passive_observer_stops_on_entry_unload_and_reloads_once(
    hass: HomeAssistant, freezer
) -> None:
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "grid": "sensor.grid",
            "batteries": [
                {"device_id": "bk", "soc": "sensor.soc", "lower": 10, "upper": 100, "inverters": []}
            ],
        },
        options={"enabled": False},
    )
    entry.add_to_hass(hass)
    with patch.object(type(hass.services), "async_call", new_callable=AsyncMock) as services:
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        original = hass.data[DOMAIN][entry.entry_id].observer
        assert original is not None and original.persistence_ok
        from custom_components.sunenergyxt_ems.diagnostics import async_get_config_entry_diagnostics

        diagnostics = await async_get_config_entry_diagnostics(hass, entry)
        assert diagnostics["demand_observation"]["persistence_ok"]
        assert diagnostics["demand_observation"]["reason"] == "grid_unavailable"
        original_poll = original.last_poll_at
        assert await hass.config_entries.async_unload(entry.entry_id)
        freezer.tick(120)
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
        await hass.async_block_till_done()
        assert original.last_poll_at == original_poll
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        replacement = hass.data[DOMAIN][entry.entry_id].observer
        assert replacement is not original
        assert replacement.persistence_ok
        assert any(e["reason"] == "observer_gap" for e in replacement.events)
        assert await hass.config_entries.async_unload(entry.entry_id)
        services.assert_not_called()


@pytest.mark.asyncio
async def test_core_json_corruption_cannot_be_mistaken_for_first_use(hass: HomeAssistant, freezer):
    from json import JSONDecodeError

    from homeassistant.exceptions import HomeAssistantError
    from homeassistant.helpers import issue_registry as ir
    from homeassistant.util import dt as dt_util

    freezer.move_to("2026-11-08T12:00:00+00:00")

    entry = MockConfigEntry(domain=DOMAIN, data={"grid": "sensor.grid", "batteries": []})
    observer = DemandObserver(
        hass, entry, lambda: HouseholdObservation(dt_util.utcnow(), 600, "measured")
    )
    target = observer._store.path

    def broken_json(path):
        if str(path) == str(target):
            try:
                raise JSONDecodeError("corrupt checkpoint", "{", 1)
            except JSONDecodeError as exc:
                raise HomeAssistantError("Cannot decode stored JSON") from exc
        return {}

    with (
        patch.object(HAStore, "_async_load", _CORE_STORE_ASYNC_LOAD),
        patch("homeassistant.helpers.storage.json_util.load_json", side_effect=broken_json),
        patch("homeassistant.helpers.storage.os.rename") as rename,
    ):
        try:
            await observer.async_start()
            assert observer.reason == "storage_unavailable"
            assert not observer.persistence_ok
            assert observer.history.intervals == ()
            assert any(
                key[1].startswith(f"storage_corruption_{DOMAIN}.demand.")
                for key in ir.async_get(hass).issues
            )
            rename.assert_called_once()
        finally:
            await observer.async_stop()


@pytest.mark.usefixtures("enable_custom_integrations")
@pytest.mark.asyncio
async def test_planning_status_registers_translates_and_preserves_disabled_ems(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"grid": "sensor.grid", "batteries": []},
        options={"enabled": False, "planning_enabled": True},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_planning_status"
    )
    assert entity_id is not None
    state = hass.states.get(entity_id)
    assert state.state == "ems_disabled"
    assert state.attributes["decision_kind"] == "forecast_priority_hint_not_measured_savings"
    runtime = hass.data[DOMAIN][entry.entry_id]
    assert runtime.controller.enabled is False
    for language in ("en", "de"):
        translations = await async_get_translations(hass, language, "entity", integrations={DOMAIN})
        assert translations[f"component.{DOMAIN}.entity.sensor.planning_status.state.ems_disabled"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert hass.states.get(entity_id).state == "unavailable"
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert (
        er.async_get(hass).async_get_entity_id(
            "sensor", DOMAIN, f"{entry.entry_id}_planning_status"
        )
        == entity_id
    )
    assert hass.states.get(entity_id).state == "ems_disabled"
    assert await hass.config_entries.async_unload(entry.entry_id)
