"""Home Assistant setup tests: device discovery, wizard steps, and input checks."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import config_flow as flow_module  # noqa: E402
from custom_components.sunenergyxt_ems.config_flow import (  # noqa: E402
    SunEnergyXTEMSConfigFlow,
)


@pytest.fixture
def flow(monkeypatch):
    obj = SunEnergyXTEMSConfigFlow()
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity: SimpleNamespace(attributes={"unit_of_measurement": "W"})
        )
    )
    obj.hass = hass
    monkeypatch.setattr(obj, "_async_current_entries", lambda: [])
    monkeypatch.setattr(
        flow_module,
        "battery_suggestions",
        lambda _: {
            "battery-1": {
                "soc": "sensor.soc",
                "lower": "number.lower",
                "upper": "number.upper",
            }
        },
    )
    monkeypatch.setattr(flow_module, "device_label", lambda hass, device: "BK215")
    monkeypatch.setattr(
        flow_module.er,
        "async_get",
        lambda hass: SimpleNamespace(async_get=lambda entity: SimpleNamespace(domain="number")),
    )
    monkeypatch.setattr(
        flow_module,
        "inverter_device_suggestions",
        lambda hass: {"number.inverter": "Deye inverter", "number.same": "Other inverter"},
    )
    monkeypatch.setattr(flow_module, "device_rated_w", lambda hass, entity: 800.0)
    monkeypatch.setattr(flow_module, "suggested_grid", lambda hass: "sensor.grid")
    return obj


@pytest.mark.asyncio
async def test_positive_setup_uses_device_instead_of_three_manual_entities(flow, monkeypatch):
    result = await flow.async_step_user()
    assert result["step_id"] == "user"

    result = await flow.async_step_user({"batteries": ["battery-1"]})
    assert result["step_id"] == "inverters"
    assert flow._batteries[0]["soc"] == "sensor.soc"
    assert flow._batteries[0]["lower"] == "number.lower"

    result = await flow.async_step_inverters({"inverters_1": ["number.inverter"]})
    assert result["step_id"] == "grid"

    monkeypatch.setattr(flow, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(flow, "_abort_if_unique_id_configured", lambda: None)
    result = await flow.async_step_grid(
        {
            "grid": "sensor.grid",
            "ack": True,
            "invert_grid": False,
        }
    )
    assert result["type"] == "create_entry"
    assert result["options"]["enabled"] is True
    assert result["options"]["feed_in"] is False


@pytest.mark.asyncio
async def test_negative_duplicate_inverter_is_rejected(flow):
    flow._batteries = [
        {"device_id": "a", "inverters": []},
        {"device_id": "b", "inverters": []},
    ]
    result = await flow.async_step_inverters(
        {
            "inverters_1": ["number.same"],
            "inverters_2": ["number.same"],
        }
    )
    assert result["errors"]["base"] == "duplicate_inverter"


@pytest.mark.asyncio
async def test_negative_setup_requires_disabling_other_controllers(flow):
    flow._batteries = [{"device_id": "a", "inverters": []}]
    result = await flow.async_step_grid({"grid": "sensor.grid", "ack": False})
    assert result["errors"]["base"] == "other_controller"


@pytest.mark.asyncio
async def test_positive_explicit_correction_step_preserves_automatic_suggestions(flow):
    result = await flow.async_step_user(
        {
            "batteries": ["battery-1"],
            "correct_values": True,
        }
    )
    assert result["step_id"] == "correct"
    result = await flow.async_step_correct(
        {
            "soc_1": "sensor.updated_soc",
            "lower_1": "number.lower",
            "upper_1": "number.upper",
        }
    )
    assert result["step_id"] == "inverters"
    assert flow._batteries[0]["soc"] == "sensor.updated_soc"


@pytest.mark.asyncio
async def test_missing_optional_upper_limit_does_not_block_initial_setup(flow, monkeypatch):
    monkeypatch.setattr(
        flow_module,
        "battery_suggestions",
        lambda _: {
            "battery-1": {
                "soc": "sensor.soc",
                "lower": "number.lower",
            }
        },
    )
    result = await flow.async_step_user({"batteries": ["battery-1"]})
    assert result["step_id"] == "inverters"


@pytest.mark.asyncio
async def test_positive_manual_battery_fallback_when_upstream_not_found(flow, monkeypatch):
    monkeypatch.setattr(flow_module, "battery_suggestions", lambda _: {})
    result = await flow.async_step_user()
    assert result["step_id"] == "battery_manual"
    result = await flow.async_step_battery_manual(
        {
            "soc": "sensor.manual_soc",
            "lower": 10,
            "upper": 90,
        }
    )
    assert result["step_id"] == "inverters"
    assert flow._batteries[0]["lower"] == 10


@pytest.mark.asyncio
async def test_negative_second_ems_config_entry_is_rejected(flow, monkeypatch):
    monkeypatch.setattr(flow, "_async_current_entries", lambda: [object()])
    result = await flow.async_step_user()
    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"


@pytest.mark.asyncio
async def test_negative_empty_battery_selection_is_rejected(flow):
    result = await flow.async_step_user({"batteries": []})
    assert result["errors"]["base"] == "no_battery"


@pytest.mark.asyncio
async def test_negative_missing_inverter_rejected(flow):
    flow._batteries = [{"device_id": "a", "inverters": []}]
    result = await flow.async_step_inverters({"inverters_1": []})
    assert result["errors"]["base"] == "invalid_inverter"


@pytest.mark.asyncio
async def test_negative_non_number_entity_rejected(flow, monkeypatch):
    flow._batteries = [{"device_id": "a", "inverters": []}]
    monkeypatch.setattr(
        flow_module.er,
        "async_get",
        lambda hass: SimpleNamespace(async_get=lambda entity: SimpleNamespace(domain="sensor")),
    )
    result = await flow.async_step_inverters({"inverters_1": ["sensor.unsupported"]})
    assert result["errors"]["base"] == "invalid_inverter"


@pytest.mark.asyncio
async def test_positive_unknown_inverter_rating_uses_manual_rating_page(flow, monkeypatch):
    flow._batteries = [{"device_id": "a", "inverters": []}]
    monkeypatch.setattr(flow_module, "device_rated_w", lambda hass, entity: None)
    result = await flow.async_step_inverters({"inverters_1": ["number.inverter"]})
    assert result["step_id"] == "ratings"
    result = await flow.async_step_ratings({"rating_0_0": 500})
    assert result["step_id"] == "grid"
    assert flow._batteries[0]["inverters"][0]["rated_w"] == 500


@pytest.mark.asyncio
async def test_negative_wrong_grid_meter_unit_rejected(flow):
    flow.hass.states.get = lambda _: SimpleNamespace(attributes={"unit_of_measurement": "kWh"})
    result = await flow.async_step_grid({"grid": "sensor.energy", "ack": True})
    assert result["errors"]["base"] == "invalid_setup"


@pytest.mark.asyncio
async def test_positive_options_flow_returns_editable_switches(flow, monkeypatch):
    options = flow_module.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    monkeypatch.setattr(
        flow_module.EMSEntryOptionsFlow,
        "config_entry",
        property(lambda _: SimpleNamespace(options={"enabled": True, "feed_in": False})),
    )
    result = await options.async_step_init()
    assert result["step_id"] == "init"
    result = await options.async_step_init({"enabled": False, "feed_in": True})
    assert result["type"] == "create_entry"
    assert result["data"]["enabled"] is False


@pytest.mark.asyncio
async def test_reconfigure_keeps_existing_batteries_and_preserves_options(flow, monkeypatch):
    entry = SimpleNamespace(
        data={
            "batteries": [
                {
                    "device_id": "battery-1",
                    "soc": "sensor.soc",
                    "lower": "number.lower",
                    "upper": "number.upper",
                    "inverters": [{"entity_id": "number.inverter", "rated_w": 800}],
                }
            ],
            "grid": "sensor.grid",
            "invert_grid": False,
        },
        options={"enabled": False, "feed_in": True},
        entry_id="ems-existing",
    )
    monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: entry)
    result = await flow.async_step_reconfigure()
    assert result["step_id"] == "reconfigure"
    result = await flow.async_step_reconfigure({"batteries": ["battery-1"]})
    assert result["step_id"] == "inverters"
    result = await flow.async_step_inverters({"inverters_1": ["number.inverter"]})
    assert result["step_id"] == "grid"
    monkeypatch.setattr(flow, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(flow, "_abort_if_unique_id_mismatch", lambda: None)
    monkeypatch.setattr(
        flow,
        "async_update_reload_and_abort",
        lambda old, *, data_updates: {
            "type": "abort",
            "reason": "reconfigure_successful",
            "data": data_updates,
        },
    )
    result = await flow.async_step_grid({"grid": "sensor.grid", "ack": True})
    assert result["reason"] == "reconfigure_successful"
    assert result["data"]["batteries"][0]["device_id"] == "battery-1"
    assert entry.options == {"enabled": False, "feed_in": True}


@pytest.mark.asyncio
async def test_reconfigure_fills_missing_sources_and_discovers_new_batteries(flow, monkeypatch):
    """Only an already configured source takes precedence over discovery."""
    entry = SimpleNamespace(
        data={"batteries": [{"device_id": "battery-1", "soc": "sensor.custom_soc", "lower": 0}]}
    )
    monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: entry)
    monkeypatch.setattr(
        flow_module,
        "battery_suggestions",
        lambda _: {
            "battery-1": {
                "soc": "sensor.discovered_soc",
                "lower": "number.discovered_lower",
                "upper": "number.discovered_upper",
            },
            "battery-2": {"soc": "sensor.second_soc", "lower": "number.second_lower"},
        },
    )
    result = await flow.async_step_reconfigure({"batteries": ["battery-1", "battery-2"]})
    assert result["step_id"] == "inverters"
    first, second = flow._batteries
    assert first["soc"] == "sensor.custom_soc"
    assert first["lower"] == 0
    assert first["upper"] == "number.discovered_upper"
    assert second["soc"] == "sensor.second_soc"
    assert second["lower"] == "number.second_lower"


@pytest.mark.asyncio
async def test_setup_options_allow_simple_grid_target(flow, monkeypatch):
    options = flow_module.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    monkeypatch.setattr(
        flow_module.EMSEntryOptionsFlow,
        "config_entry",
        property(
            lambda _: SimpleNamespace(
                options={
                    "enabled": True,
                    "feed_in": False,
                    "grid_target_w": 20,
                }
            )
        ),
    )
    result = await options.async_step_init()
    assert "grid_target_w" in [key.schema for key in result["data_schema"].schema]
    new = await options.async_step_init(
        {
            "enabled": True,
            "feed_in": False,
            "grid_target_w": 35,
        }
    )
    assert new["data"]["grid_target_w"] == 35


@pytest.mark.asyncio
async def test_detected_inverters_are_offered_by_device_label(flow):
    flow._batteries = [{"device_id": "battery-1", "inverters": []}]
    result = await flow.async_step_inverters()
    choices = result["data_schema"].schema
    fields = [
        schema
        for schema in choices.values()
        if isinstance(schema, flow_module.selector.SelectSelector)
    ]
    assert fields
    assert any(option["label"] == "Deye inverter" for option in fields[0].config["options"])


@pytest.mark.asyncio
async def test_manual_inverter_fallback_keeps_unknown_brands(flow):
    flow._batteries = [{"device_id": "battery-1", "inverters": []}]
    result = await flow.async_step_inverters({"manual_inverters": True})
    assert result["step_id"] == "inverters_manual"
    result = await flow.async_step_inverters_manual({"inverters_1": ["number.inverter"]})
    assert result["step_id"] == "grid"


@pytest.mark.asyncio
async def test_reconfigure_safely_stops_old_controller_first(flow, monkeypatch):
    entry = SimpleNamespace(
        entry_id="ems-existing", data={"batteries": [], "grid": "sensor.grid", "invert_grid": False}
    )
    controller = SimpleNamespace(
        async_stop=AsyncMock(),
        async_disable=AsyncMock(),
        last_error=None,
    )
    flow._reconfigure = True
    flow.hass.data = {"sunenergyxt_ems": {"ems-existing": controller}}
    monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: entry)
    monkeypatch.setattr(flow, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(flow, "_abort_if_unique_id_mismatch", lambda: None)
    monkeypatch.setattr(
        flow,
        "async_update_reload_and_abort",
        lambda entry, *, data_updates: {"type": "abort", "reason": "reconfigure_successful"},
    )
    result = await flow.async_step_grid({"grid": "sensor.grid", "ack": True})
    controller.async_stop.assert_not_awaited()
    controller.async_disable.assert_awaited_once()
    assert result["reason"] == "reconfigure_successful"


@pytest.mark.asyncio
async def test_reconfigure_refuses_to_switch_if_old_inverter_cannot_stop(
    flow,
    monkeypatch,
):
    entry = SimpleNamespace(entry_id="ems-existing", data={})
    controller = SimpleNamespace(
        async_stop=AsyncMock(),
        async_disable=AsyncMock(),
        last_error="number.inverter: unavailable",
    )
    flow._reconfigure = True
    flow.hass.data = {"sunenergyxt_ems": {"ems-existing": controller}}
    monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: entry)
    monkeypatch.setattr(flow, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(flow, "_abort_if_unique_id_mismatch", lambda: None)
    result = await flow.async_step_grid({"grid": "sensor.grid", "ack": True})
    assert result["errors"]["base"] == "inverter_stop_failed"


@pytest.mark.asyncio
async def test_reconfigure_never_reassigns_inverters_before_shutdown_ack(flow, monkeypatch):
    entry = SimpleNamespace(entry_id="ems-existing", data={})
    runtime = SimpleNamespace(
        async_stop=AsyncMock(),
        async_disable=AsyncMock(),
        last_error=None,
        _shutdown_pending={"number.inverter": object()},
    )
    flow._reconfigure = True
    flow.hass.data = {"sunenergyxt_ems": {"ems-existing": runtime}}
    monkeypatch.setattr(flow, "_get_reconfigure_entry", lambda: entry)
    monkeypatch.setattr(flow, "async_set_unique_id", AsyncMock())
    monkeypatch.setattr(flow, "_abort_if_unique_id_mismatch", lambda: None)
    result = await flow.async_step_grid({"grid": "sensor.grid", "ack": True})
    assert result["errors"]["base"] == "inverter_stop_failed"


@pytest.mark.asyncio
async def test_reject_number_entities_that_are_not_power_limits(flow):
    flow._batteries = [{"device_id": "battery-1", "inverters": []}]
    flow.hass.states.get = lambda _: SimpleNamespace(attributes={"unit_of_measurement": "Hz"})
    result = await flow.async_step_inverters({"inverters_1": ["number.inverter"]})
    assert result["errors"]["base"] == "invalid_inverter"


@pytest.mark.asyncio
async def test_feed_in_enabling_asks_for_missing_upper_limit(flow, monkeypatch):
    entry = SimpleNamespace(
        data={
            "batteries": [
                {"device_id": "bk1", "soc": "sensor.soc", "lower": "number.lower", "inverters": []}
            ]
        },
        options={"enabled": True, "feed_in": False},
    )
    options = flow_module.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    monkeypatch.setattr(
        flow_module.EMSEntryOptionsFlow,
        "config_entry",
        property(lambda _: entry),
    )
    updates = []
    options.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=lambda cfg, *, data: updates.append(data))
    )
    result = await options.async_step_init({"enabled": True, "feed_in": True, "grid_target_w": 20})
    assert result["errors"]["base"] == "missing_upper"
    assert not updates
    result = await options.async_step_init(
        {"enabled": True, "feed_in": True, "grid_target_w": 20, "upper_1": 90}
    )
    assert result["type"] == "create_entry"
    assert updates[0]["batteries"][0]["upper"] == 90
    assert "upper_1" not in result["data"]


@pytest.mark.asyncio
async def test_options_display_household_and_pv_modes(flow, monkeypatch):
    options = flow_module.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    monkeypatch.setattr(
        flow_module.EMSEntryOptionsFlow,
        "config_entry",
        property(
            lambda _: SimpleNamespace(
                data={"batteries": []},
                options={"enabled": False, "feed_in": False, "grid_target_w": 20},
            )
        ),
    )
    result = await options.async_step_init()
    assert "feed_in_mode" in [key.schema for key in result["data_schema"].schema]
    saved = await options.async_step_init(
        {"enabled": False, "feed_in": True, "feed_in_mode": "household", "grid_target_w": 20}
    )
    assert saved["data"]["feed_in_mode"] == "household"


@pytest.mark.asyncio
async def test_planning_options_preserve_controller_tuning_and_source_maps(monkeypatch):
    entry = SimpleNamespace(
        data={"batteries": []},
        options={
            "enabled": False,
            "feed_in": False,
            "pid_ki": 0.07,
            "pv_forecast": {
                "today": "sensor.today",
                "tomorrow": "sensor.tomorrow",
                "updated": "sensor.updated",
            },
        },
    )
    options = flow_module.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    monkeypatch.setattr(flow_module.EMSEntryOptionsFlow, "config_entry", property(lambda _: entry))
    saved = await options.async_step_init({"planning_enabled": True})
    assert saved["data"]["planning_enabled"] is True
    assert saved["data"]["pid_ki"] == 0.07
    assert saved["data"]["enabled"] is False
    assert saved["data"]["pv_forecast"] == entry.options["pv_forecast"]


@pytest.mark.asyncio
async def test_tripped_dc_output_cannot_be_reset_while_enabled(monkeypatch):
    options = flow_module.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    entry = SimpleNamespace(
        data={"batteries": [{"device_id": "bk1", "dc_output": "sensor.dc"}]},
        options={"enabled": False, "dc_tripped_batteries": ["bk1"]},
    )
    monkeypatch.setattr(flow_module.EMSEntryOptionsFlow, "config_entry", property(lambda _: entry))
    result = await options.async_step_init(
        {"enabled": True, "feed_in": False, "reset_dc_trip": True}
    )
    assert result["errors"]["base"] == "disable_before_trip_reset"


@pytest.mark.asyncio
async def test_tripped_dc_output_reset_requires_fresh_zero_watts(monkeypatch):
    options = flow_module.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    entry = SimpleNamespace(
        data={"batteries": [{"device_id": "bk1", "dc_output": "sensor.dc"}]},
        options={"enabled": False, "dc_tripped_batteries": ["bk1"]},
    )
    monkeypatch.setattr(flow_module.EMSEntryOptionsFlow, "config_entry", property(lambda _: entry))
    state = SimpleNamespace(
        state="0",
        attributes={"unit_of_measurement": "W"},
        last_reported=datetime.now(UTC) - timedelta(minutes=5),
    )
    monkeypatch.setattr(
        flow_module.EMSEntryOptionsFlow,
        "hass",
        property(lambda _: SimpleNamespace(states=SimpleNamespace(get=lambda entity: state))),
    )
    result = await options.async_step_init(
        {"enabled": False, "feed_in": False, "reset_dc_trip": True}
    )
    assert result["errors"]["base"] == "trip_reset_unconfirmed"

    state.last_reported = datetime.now(UTC)
    saved = await options.async_step_init(
        {"enabled": False, "feed_in": False, "reset_dc_trip": True}
    )
    assert saved["type"] == "create_entry"
    assert saved["data"]["dc_tripped_batteries"] == []


@pytest.mark.asyncio
async def test_legacy_entry_can_reset_dc_trip_with_registered_sensor(monkeypatch):
    """A migrated entry has no dc_output field but the official device still does."""
    options = flow_module.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    entry = SimpleNamespace(
        data={"batteries": [{"device_id": "bk1"}]},
        options={"enabled": False, "dc_tripped_batteries": ["bk1"]},
    )
    monkeypatch.setattr(flow_module.EMSEntryOptionsFlow, "config_entry", property(lambda _: entry))
    state = SimpleNamespace(
        state="0",
        attributes={"unit_of_measurement": "W"},
        last_reported=datetime.now(UTC),
    )
    monkeypatch.setattr(
        flow_module.EMSEntryOptionsFlow,
        "hass",
        property(lambda _: SimpleNamespace(states=SimpleNamespace(get=lambda key: state))),
    )
    monkeypatch.setattr(
        flow_module, "battery_suggestions", lambda hass: {"bk1": {"dc_output": "sensor.dc"}}
    )
    result = await options.async_step_init(
        {"enabled": False, "feed_in": False, "reset_dc_trip": True}
    )
    assert result["type"] == "create_entry"
    assert result["data"]["dc_tripped_batteries"] == []
