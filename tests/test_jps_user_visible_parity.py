"""User-visible JPS feature parity acceptance, not just code or options keys.

These initially fail against v0.1.0b5: no PID control switch, no tuning number
platform, no dedicated per-tower EV/bypass or per-inverter diagnostics, and an
existing BK215 upper SoC source cannot be edited via Configure.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")

from custom_components.sunenergyxt_ems import config_flow, sensor, switch  # noqa: E402
from custom_components.sunenergyxt_ems.logic import Controller, StartHold  # noqa: E402


def entry_and_runtime():
    entry = SimpleNamespace(
        entry_id="jps-parity",
        data={
            "batteries": [
                {
                    "device_id": "bk1",
                    "label": "BK215 1",
                    "soc": "sensor.soc",
                    "lower": 10,
                    "upper": "number.bk215_limit",
                    "inverters": [{"entity_id": "number.deye_m200", "rated_w": 2000}],
                },
                {
                    "device_id": "bk2",
                    "label": "BK215 2",
                    "soc": "sensor.soc2",
                    "lower": 10,
                    "upper": 90,
                    "inverters": [{"entity_id": "number.deye_m80", "rated_w": 800}],
                },
            ]
        },
        options={
            "enabled": False,
            "feed_in": False,
            "adaptive_pid": False,
            "start_hold_seconds": 30,
            "grid_target_w": 20,
        },
    )
    writes = []

    def change(_, *, options=None, data=None):
        if options is not None:
            entry.options = options
        if data is not None:
            entry.data = data
        writes.append((options, data))

    runtime = SimpleNamespace(
        entry=entry,
        status="disabled",
        last_error=None,
        signal="test_ems_signal",
        controller=Controller(),
        _last_sent={},
        _pending={},
        _shutdown_pending={},
        _deferred_shutdown=set(),
        _start_hold=StartHold(seconds=30),
        _grid_raw_w=None,
        _grid_filtered_w=None,
        _dc_monitored={},
        _reported_outputs=lambda batteries: {},
    )
    runtime.controller.enabled = False
    runtime.hass = SimpleNamespace(
        data={"sunenergyxt_ems": {entry.entry_id: runtime}},
        config_entries=SimpleNamespace(async_update_entry=change),
    )
    return entry, runtime, writes


@pytest.mark.asyncio
async def test_pid_is_directly_discoverable_and_switchable_in_ha():
    entry, runtime, writes = entry_and_runtime()
    entities = []
    await switch.async_setup_entry(runtime.hass, entry, entities.extend)
    by_id = {e.unique_id: e for e in entities}
    pid = by_id[f"{entry.entry_id}_adaptive_pid"]
    assert pid.is_on is False
    pid.async_write_ha_state = lambda: None
    await pid.async_turn_on()
    assert entry.options["adaptive_pid"] is True
    assert entry.options["enabled"] is False
    assert writes[-1][1] is None


@pytest.mark.asyncio
async def test_startup_hold_and_pid_gains_are_native_ha_number_entities():
    from custom_components.sunenergyxt_ems import number  # noqa: PLC0415

    entry, runtime, _writes = entry_and_runtime()
    entities = []
    await number.async_setup_entry(runtime.hass, entry, entities.extend)
    by_id = {e.unique_id: e for e in entities}
    assert f"{entry.entry_id}_start_hold_seconds" in by_id
    assert f"{entry.entry_id}_pid_ki" in by_id
    assert by_id[f"{entry.entry_id}_start_hold_seconds"].native_value == 30
    hold = by_id[f"{entry.entry_id}_start_hold_seconds"]
    hold.async_write_ha_state = lambda: None
    await hold.async_set_native_value(40)
    assert entry.options["start_hold_seconds"] == 40
    assert entry.options["enabled"] is False


@pytest.mark.asyncio
async def test_existing_bk215_upper_soc_mapping_can_be_changed_in_options(monkeypatch):
    entry, runtime, _writes = entry_and_runtime()
    handler = config_flow.EMSEntryOptionsFlow()
    monkeypatch.setattr(
        config_flow.EMSEntryOptionsFlow, "config_entry", property(lambda _: entry)
    )
    handler.hass = runtime.hass
    form = await handler.async_step_init()
    fields = {
        getattr(marker, "schema", str(marker)) for marker in form["data_schema"].schema
    }
    assert "upper_source_1" in fields
    assert "upper_pct_2" in fields
    result = await handler.async_step_init(
        {
            "enabled": False,
            "feed_in": False,
            "upper_source_1": "sensor.bk215_hw_charge_limit",
        }
    )
    assert result["type"] == "create_entry"
    assert entry.data["batteries"][0]["upper"] == "sensor.bk215_hw_charge_limit"
    assert entry.data["batteries"][1]["upper"] == 90
    assert entry.options["enabled"] is False


@pytest.mark.asyncio
async def test_one_sensor_each_for_bypass_ev_and_per_inverter_diagnostics(monkeypatch):
    entry, runtime, _writes = entry_and_runtime()
    runtime.controller.state.full = set()
    runtime.controller.state.ev3600_active = set()
    runtime.controller.state.ev3600_unknown = set()
    runtime._batteries = lambda: (
        SimpleNamespace(
            key="bk1", soc=89, upper_soc=90, lower_soc=10, pv_input_w=None,
            ev3600_charging=False, dc_output_w=0,
            inverters=(SimpleNamespace(key="number.deye_m200", online=True),),
        ),
        SimpleNamespace(
            key="bk2", soc=11, upper_soc=90, lower_soc=10, pv_input_w=None,
            ev3600_charging=False, dc_output_w=0,
            inverters=(SimpleNamespace(key="number.deye_m80", online=True),),
        ),
    )
    monkeypatch.setattr(
        sensor.dr,
        "async_get",
        lambda _: SimpleNamespace(
            async_get_or_create=lambda **_kwargs: SimpleNamespace(id="parent-device")
        ),
    )
    entities = []
    await sensor.async_setup_entry(runtime.hass, entry, entities.extend)
    by_id = {e.unique_id: e for e in entities}
    for key in ("bk1", "bk2"):
        assert f"{entry.entry_id}_battery_{key}_ev3600_state" in by_id
        assert f"{entry.entry_id}_battery_{key}_bypass_state" in by_id
    assert by_id[f"{entry.entry_id}_battery_bk1_ev3600_state"].native_value == "not_configured"
    assert by_id[f"{entry.entry_id}_battery_bk1_bypass_state"].native_value == "disabled"
    assert f"{entry.entry_id}_inverter_number.deye_m200_requested" in by_id
    assert f"{entry.entry_id}_inverter_number.deye_m200_reported" in by_id
    assert f"{entry.entry_id}_inverter_number.deye_m200_measured_ac" in by_id
    # A missing independent AC source is not displayed as a fabricated 0W output.
    assert by_id[f"{entry.entry_id}_inverter_number.deye_m200_measured_ac"].native_value is None
