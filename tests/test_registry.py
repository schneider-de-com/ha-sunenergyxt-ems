"""Registry discovery tests using the real HA module and fake registries."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import registry as module  # noqa: E402
from custom_components.sunenergyxt_ems.discovery import Candidate  # noqa: E402


def item(entity_id, unique_id, device, platform, domain="sensor"):
    return SimpleNamespace(
        entity_id=entity_id,
        unique_id=unique_id,
        device_id=device,
        platform=platform,
        domain=domain,
        translation_key=None,
    )


@pytest.fixture
def registry(monkeypatch):
    entries = [
        item("sensor.renamed", "device123_wifi_soc", "bk1", "sunEnergyXT"),
        item("number.low", "device123_wifi_cnfg_disc_soc_min", "bk1", "sunEnergyXT", "number"),
        item("number.high", "device123_wifi_cnfg_chg_soc_max", "bk1", "sunEnergyXT", "number"),
        item("sensor.input", "device123_wifi_inputpower_total", "bk1", "sunEnergyXT"),
        item("sensor.grid", "device123_total_active_power", "meter", "shelly"),
        item("sensor.unrelated", "device456_wifi_soc", "other", "other"),
    ]
    units = {
        "sensor.renamed": "%",
        "number.low": "%",
        "number.high": "%",
        "sensor.input": "W",
        "sensor.grid": "W",
        "sensor.unrelated": "%",
    }
    hass = SimpleNamespace(
        states=SimpleNamespace(
            get=lambda key: SimpleNamespace(
                attributes={"unit_of_measurement": units[key]}
            ) if key in units else None
        )
    )
    monkeypatch.setattr(
        module.er, "async_get",
        lambda _: SimpleNamespace(entities={entry.entity_id: entry for entry in entries}),
    )
    return hass


def test_positive_registry_uses_unique_ids_after_user_renames(registry):
    groups = module.battery_suggestions(registry)
    assert groups["bk1"] == {
        "soc": "sensor.renamed",
        "lower": "number.low",
        "upper": "number.high",
        "charging": "sensor.input",
    }


def test_positive_grid_meter_suggestion_uses_real_power_units(registry):
    assert module.suggested_grid(registry) == "sensor.grid"


def test_negative_other_integration_is_not_detected_as_bk215(registry):
    assert "other" not in module.battery_suggestions(registry)


def test_positive_device_label_uses_user_name(registry, monkeypatch):
    monkeypatch.setattr(module.dr, "async_get", lambda _: SimpleNamespace(
        async_get=lambda _: SimpleNamespace(
            name_by_user="Basement battery", name="BK215", model="BK215"
        )
    ))
    assert module.device_label(registry, "bk1") == "Basement battery"


def test_positive_device_label_falls_back_to_original_name(registry, monkeypatch):
    monkeypatch.setattr(module.dr, "async_get", lambda _: SimpleNamespace(
        async_get=lambda _: SimpleNamespace(name_by_user=None, name="BK215")
    ))
    assert module.device_label(registry, "bk1") == "BK215"


def test_negative_missing_device_uses_stable_id(registry, monkeypatch):
    monkeypatch.setattr(module.dr, "async_get", lambda _: SimpleNamespace(
        async_get=lambda _: None
    ))
    assert module.device_label(registry, "bk1") == "bk1"


@pytest.mark.parametrize("model,rating", [
    ("SUN-M200G4-EU-Q0", 2000),
    ("SUN-M80G4-EU-Q0", 800),
])
def test_positive_known_d_eye_models_supply_watt_rating(registry, monkeypatch, model, rating):
    monkeypatch.setattr(module.er, "async_get", lambda _: SimpleNamespace(
        async_get=lambda _: SimpleNamespace(device_id="inverter-dev")
    ))
    monkeypatch.setattr(module.dr, "async_get", lambda _: SimpleNamespace(
        async_get=lambda _: SimpleNamespace(model=model)
    ))
    registry.states.get = lambda _: SimpleNamespace(
        attributes={"unit_of_measurement": "%"}
    )
    assert module.device_rated_w(registry, "number.limit") == rating


def test_positive_watt_limit_uses_device_max_without_model(registry):
    registry.states.get = lambda _: SimpleNamespace(
        attributes={"unit_of_measurement": "W", "max": 700}
    )
    assert module.device_rated_w(registry, "number.limit") == 700


def test_negative_unknown_rating_requires_manual_entry(registry, monkeypatch):
    registry.states.get = lambda _: SimpleNamespace(
        attributes={"unit_of_measurement": "%"}
    )
    monkeypatch.setattr(module.er, "async_get", lambda _: SimpleNamespace(
        async_get=lambda _: None
    ))
    assert module.device_rated_w(registry, "number.limit") is None


def test_negative_missing_state_has_no_rating(registry):
    registry.states.get = lambda _: None
    assert module.device_rated_w(registry, "number.limit") is None


def test_negative_unknown_model_has_no_inferred_rating(registry, monkeypatch):
    registry.states.get = lambda _: SimpleNamespace(
        attributes={"unit_of_measurement": "%"}
    )
    monkeypatch.setattr(module.er, "async_get", lambda _: SimpleNamespace(
        async_get=lambda _: SimpleNamespace(device_id="dev")
    ))
    monkeypatch.setattr(module.dr, "async_get", lambda _: SimpleNamespace(
        async_get=lambda _: SimpleNamespace(model="unknown")
    ))
    assert module.device_rated_w(registry, "number.limit") is None
