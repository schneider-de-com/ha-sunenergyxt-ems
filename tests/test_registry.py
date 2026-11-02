"""Live-derived registry contracts for SunEnergyXT and HA Energy.

Entity IDs below are arbitrary renameable test fixtures. Source roles are
identified only by the upstream unique IDs verified on HA 2026.10.4.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import registry as module  # noqa: E402


def item(entity_id, unique_id, device, platform, domain="sensor", *, config=None, disabled=None):
    return SimpleNamespace(
        entity_id=entity_id,
        unique_id=unique_id,
        device_id=device,
        platform=platform,
        domain=domain,
        config_entry_id=config,
        disabled_by=disabled,
        translation_key=None,
    )


@pytest.fixture
def registry(monkeypatch):
    entries = [
        item("sensor.changed_soc", "entry_a_wifi_soc", "bk1", "sunEnergyXT", config="entry_a"),
        item("number.changed_low", "entry_a_wifi_cnfg_disc_soc_min",
             "bk1", "sunEnergyXT", "number", config="entry_a"),
        item("number.changed_high", "entry_a_wifi_cnfg_chg_soc_max",
             "bk1", "sunEnergyXT", "number", config="entry_a"),
        item("sensor.changed_input", "entry_a_wifi_inputpower_total",
             "bk1", "sunEnergyXT", config="entry_a"),
        item("sensor.other_bk_soc", "entry_b_wifi_soc",
             "bk2", "sunEnergyXT", config="entry_b"),
        item("sensor.grid_renamed", "energy_power_grid_combined_meter_a_meter_b",
             "grid_meter", "energy"),
        item("number.changed_inverter", "solarman_entry_active_power_regulation_number",
             "inverter", "solarman", "number"),
        item("sensor.fake_bk", "other_wifi_soc", "other", "other", config="other"),
    ]
    units = {
        "sensor.changed_soc": "%",
        "number.changed_low": "%",
        "number.changed_high": "%",
        "sensor.changed_input": "W",
        "sensor.other_bk_soc": "%",
        "sensor.grid_renamed": "W",
        "number.changed_inverter": "%",
        "sensor.fake_bk": "%",
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda key: SimpleNamespace(
                attributes={"unit_of_measurement": units[key]}
            ) if key in units else None
        )
    )
    values = {entry.entity_id: entry for entry in entries}
    monkeypatch.setattr(
        module.er, "async_get",
        lambda _: SimpleNamespace(entities=values, async_get=values.get),
    )
    return hass, values


def test_live_derived_battery_roles_still_work_after_renames(registry):
    hass, _ = registry
    assert module.battery_suggestions(hass) == {
        "bk1": {
            "soc": "sensor.changed_soc",
            "lower": "number.changed_low",
            "upper": "number.changed_high",
            "charging": "sensor.changed_input",
        },
        "bk2": {"soc": "sensor.other_bk_soc"},
    }


def test_live_derived_energy_meter_is_suggested_after_rename(registry):
    hass, _ = registry
    assert module.suggested_grid(hass) == "sensor.grid_renamed"


def test_live_derived_solarman_inverter_is_identified_by_unique_id(registry):
    hass, _ = registry
    inverter = next(
        item for item in module.registry_candidates(hass)
        if item.entity_id == "number.changed_inverter"
    )
    assert inverter.translation_key == "active_power_regulation"
    assert inverter.unit == "%"


def test_other_integration_cannot_masquerade_as_sunenergyxt(registry):
    hass, _ = registry
    assert "other" not in module.battery_suggestions(hass)


def test_false_config_entry_prefix_does_not_match_bk215(registry):
    hass, values = registry
    values["sensor.changed_soc"].unique_id = "wrong_entry_wifi_soc"
    groups = module.battery_suggestions(hass)
    assert "bk1" not in groups


def test_disabled_battery_entity_is_not_suggested(registry):
    hass, values = registry
    values["sensor.changed_soc"].disabled_by = "user"
    assert "bk1" not in module.battery_suggestions(hass)


def test_wrong_unit_does_not_pass_battery_validation(registry):
    hass, _ = registry
    original = hass.states.get
    hass.states.get = lambda key: (
        SimpleNamespace(attributes={"unit_of_measurement": "kWh"})
        if key == "sensor.changed_soc" else original(key)
    )
    assert "bk1" not in module.battery_suggestions(hass)


def test_unknown_unique_id_never_autoselects_any_role(registry):
    hass, values = registry
    values["sensor.changed_soc"].unique_id = "entry_a_wifi_mystery"
    assert "bk1" not in module.battery_suggestions(hass)


def test_device_label_prefer_user_name(registry, monkeypatch):
    hass, _ = registry
    monkeypatch.setattr(
        module.dr, "async_get",
        lambda _: SimpleNamespace(
            async_get=lambda _: SimpleNamespace(
                name_by_user="My battery", name="BK215"
            )
        ),
    )
    assert module.device_label(hass, "bk1") == "My battery"


def test_device_label_fallback_name_or_id(registry, monkeypatch):
    hass, _ = registry
    monkeypatch.setattr(
        module.dr, "async_get",
        lambda _: SimpleNamespace(
            async_get=lambda _: SimpleNamespace(name_by_user=None, name="BK215")
        ),
    )
    assert module.device_label(hass, "bk1") == "BK215"
    monkeypatch.setattr(
        module.dr, "async_get",
        lambda _: SimpleNamespace(async_get=lambda _: None),
    )
    assert module.device_label(hass, "bk1") == "bk1"


def test_percent_limit_never_guesses_rated_watts_from_inverter_name(registry):
    hass, _ = registry
    assert module.device_rated_w(hass, "number.changed_inverter") is None


def test_watt_limit_with_explicit_max_sets_rating(registry):
    hass, _ = registry
    original = hass.states.get
    hass.states.get = lambda key: (
        SimpleNamespace(attributes={"unit_of_measurement": "W", "max": 700})
        if key == "number.changed_inverter" else original(key)
    )
    assert module.device_rated_w(hass, "number.changed_inverter") == 700


@pytest.mark.parametrize("bad", [-1, 0, 6000, None])
def test_invalid_nominal_rating_is_not_suggested(registry, bad):
    hass, _ = registry
    original = hass.states.get
    hass.states.get = lambda key: (
        SimpleNamespace(attributes={"unit_of_measurement": "W", "max": bad})
        if key == "number.changed_inverter" else original(key)
    )
    assert module.device_rated_w(hass, "number.changed_inverter") is None


def test_missing_inverter_state_gives_no_inferred_rating(registry):
    hass, _ = registry
    original = hass.states.get
    hass.states.get = lambda key: None if key == "number.changed_inverter" else original(key)
    assert module.device_rated_w(hass, "number.changed_inverter") is None
