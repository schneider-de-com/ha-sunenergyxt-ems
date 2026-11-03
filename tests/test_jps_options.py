"""Options-flow behavior for all five JPS-inspired features."""

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import config_flow as flow  # noqa: E402


@pytest.fixture
def options(monkeypatch):
    handler = flow.SunEnergyXTEMSConfigFlow.async_get_options_flow(None)
    entry = SimpleNamespace(
        data={"batteries": [{
            "device_id": "bk1",
            "upper": 90,
            "inverters": [{"entity_id": "number.m200", "rated_w": 2000}],
        }]},
        options={"enabled": False, "feed_in": False},
    )
    monkeypatch.setattr(
        flow.EMSEntryOptionsFlow, "config_entry", property(lambda _: entry)
    )
    return handler, entry


@pytest.mark.asyncio
async def test_options_save_pid_start_hold_full_pv_ev_and_measured_ac(options):
    handler, _entry = options
    result = await handler.async_step_init({
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
    })
    assert result["type"] == "create_entry"
    saved = result["data"]
    assert saved["adaptive_pid"] is True
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
    result = await handler.async_step_init({
        "enabled": False,
        "feed_in": False,
        "ev_mode_1": "switch.ev3600",
    })
    assert result["errors"]["base"] == "incomplete_ev3600"


@pytest.mark.asyncio
async def test_options_refuse_inverted_pid_gains(options):
    handler, _entry = options
    result = await handler.async_step_init({
        "enabled": False,
        "feed_in": False,
        "pid_kp_min": 1.0,
        "pid_kp_max": 0.5,
    })
    assert result["errors"]["base"] == "invalid_pid_gains"
