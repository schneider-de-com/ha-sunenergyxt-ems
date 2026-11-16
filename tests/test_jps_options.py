"""Options-flow behavior for all five JPS-inspired features."""

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import config_flow as flow  # noqa: E402


@pytest.fixture
def options(monkeypatch):
    handler = flow.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    handler.hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda entity: SimpleNamespace(attributes={"unit_of_measurement": "W"})
        )
    )
    entry = SimpleNamespace(
        data={
            "batteries": [
                {
                    "device_id": "bk1",
                    "upper": 90,
                    "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
                }
            ]
        },
        options={"enabled": False, "feed_in": False},
    )
    monkeypatch.setattr(flow.EMSEntryOptionsFlow, "config_entry", property(lambda _: entry))
    return handler, entry


@pytest.mark.asyncio
async def test_options_save_total_capacity_including_expansion(options):
    handler, _entry = options
    result = await handler.async_step_init(
        {"enabled": False, "feed_in": False, "capacity_wh_1": 4300}
    )
    assert result["type"] == "create_entry"
    assert result["data"]["capacity_wh"] == {"bk1": 4300}


@pytest.mark.asyncio
async def test_options_ignore_legacy_pid_and_save_pv_ev_ac(options):
    handler, entry = options
    entry.options["adaptive_pid"] = True
    result = await handler.async_step_init(
        {
            "enabled": False,
            "feed_in": True,
            "feed_in_mode": "pv",
            "adaptive_pid": True,
            "pid_kp_min": 0.5,
            "pid_kp_max": 0.9,
            "pid_kp_export": 1.0,
            "pid_ki": 0.02,
            "pid_kd": 0.12,
            "start_hold_seconds": 30,
            "pv_input_1": "sensor.pv",
            "ev_mode_1": "switch.ev3600",
            "ev_power_1": "sensor.ev3600",
            "ac_1_1": "sensor.m200_ac",
        }
    )
    assert result["type"] == "create_entry"
    saved = result["data"]
    assert "adaptive_pid" not in saved
    assert "pid_ki" not in saved
    assert saved["start_hold_seconds"] == 30
    assert saved["pv_input"]["bk1"] == "sensor.pv"
    assert saved["ev3600"]["bk1"] == {
        "mode": "switch.ev3600",
        "power": "sensor.ev3600",
    }
    assert saved["ac_power"]["number.m200"] == "sensor.m200_ac"


@pytest.mark.asyncio
async def test_options_refuse_unpaired_ev3600_source(options):
    handler, _entry = options
    result = await handler.async_step_init(
        {
            "enabled": False,
            "feed_in": False,
            "ev_mode_1": "switch.ev3600",
        }
    )
    assert result["errors"]["base"] == "incomplete_ev3600"


@pytest.mark.asyncio
async def test_options_ignore_obsolete_pid_gains(options):
    handler, _entry = options
    result = await handler.async_step_init(
        {
            "enabled": False,
            "feed_in": False,
            "pid_kp_min": 1.0,
            "pid_kp_max": 0.5,
        }
    )
    assert result["type"] == "create_entry"
    assert "pid_kp_min" not in result["data"]
    assert "pid_kp_max" not in result["data"]


@pytest.mark.asyncio
async def test_basic_save_preserves_existing_pv_mapping_when_sensor_is_offline(options):
    """Changing an unrelated target does not require optional PV to be online."""
    handler, entry = options
    entry.options.update(
        {
            "pv_input": {"bk1": "sensor.offline_pv"},
            "capacity_wh": {"bk1": 4300},
            "grid_target_w": 20,
            "start_hold_seconds": 40,
        }
    )
    handler.hass.states.get = lambda _entity: None
    result = await handler.async_step_basic({"enabled": False, "grid_target_w": 35})
    assert result["type"] == "create_entry"
    assert result["data"]["grid_target_w"] == 35
    assert result["data"]["pv_input"] == {"bk1": "sensor.offline_pv"}
    assert result["data"]["capacity_wh"] == {"bk1": 4300}
    assert result["data"]["start_hold_seconds"] == 40


@pytest.mark.asyncio
async def test_device_page_rejects_new_invalid_pv_mapping_but_retains_other_options(options):
    handler, entry = options
    entry.options["pv_input"] = {"bk1": "sensor.existing_pv"}
    handler.hass.states.get = lambda _entity: None
    result = await handler.async_step_devices({"pv_input_1": "sensor.invalid_pv"})
    assert result["type"] == "form"
    assert result["errors"]["pv_input_1"] == "invalid_pv_power_source"
    assert entry.options["pv_input"] == {"bk1": "sensor.existing_pv"}


@pytest.mark.asyncio
async def test_old_options_payload_cannot_reenable_pid_without_explicit_migration(options):
    handler, entry = options
    entry.options["adaptive_pid"] = False
    result = await handler.async_step_init({"enabled": False, "adaptive_pid": True})

    assert result["type"] == "create_entry"
    assert "adaptive_pid" not in result["data"]
