"""Priority-3 inputs: upstream ownership and unambiguous migration."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

pytest.importorskip("homeassistant")
from custom_components.sunenergyxt_ems import registry as module  # noqa: E402
from custom_components.sunenergyxt_ems.const import (  # noqa: E402
    FEED_IN_MODE_HOUSEHOLD,
    FEED_IN_MODE_PV,
    configured_export_mode,
)


def registered(entity_id, unique_id, device_id, platform, entry_id, domain="sensor"):
    return SimpleNamespace(
        entity_id=entity_id,
        unique_id=unique_id,
        device_id=device_id,
        platform=platform,
        domain=domain,
        config_entry_id=entry_id,
        disabled_by=None,
    )


def setup_fake(monkeypatch, *, rated=2000, valid_head=True, expansion=True):
    items = {
        "number.inv": registered(
            "number.inv", "solarman_a_active_power_regulation_number",
            "inverter-a", "solarman", "solarman_a", "number"
        ),
        "sensor.device": registered(
            "sensor.device", "solarman_a_device_sensor",
            "inverter-a", "solarman", "solarman_a"
        ),
        "sensor.bk": registered(
            "sensor.bk", "bk_a_wifi_soc", "bk-a", "sunenergyxt_bk", "bk_a"
        ),
        "sensor.head": registered(
            "sensor.head", "bk_a_wifi_b_actual_soc", "bk-a", "sunenergyxt_bk", "bk_a"
        ),
        "sensor.head_limit": registered(
            "sensor.head_limit", "bk_a_wifi_mbms_hw_limited_chg_soc_max",
            "bk-a", "sunenergyxt_bk", "bk_a"
        ),
    }
    if expansion:
        items["sensor.ext"] = registered(
            "sensor.ext", "bk_a_wifi_b1_actual_soc", "bk-a", "sunenergyxt_bk", "bk_a"
        )
        items["sensor.ext_limit"] = registered(
            "sensor.ext_limit", "bk_a_wifi_sbms1_hw_limited_chg_soc_max",
            "bk-a", "sunenergyxt_bk", "bk_a"
        )
    stamp = datetime.now(UTC)
    states = {
        "number.inv": SimpleNamespace(
            state="0", attributes={"unit_of_measurement": "%"}
        ),
        "sensor.device": SimpleNamespace(
            state="Microinverter", attributes={"Rated Power": rated}
        ),
        "sensor.head": SimpleNamespace(
            state="80" if valid_head else "unavailable",
            attributes={"unit_of_measurement": "%"},
            last_reported=stamp, last_updated=stamp
        ),
        "sensor.head_limit": SimpleNamespace(
            state="90", attributes={"unit_of_measurement": "%"},
            last_reported=stamp, last_updated=stamp
        ),
        "sensor.ext": SimpleNamespace(
            state="75", attributes={"unit_of_measurement": "%"},
            last_reported=stamp, last_updated=stamp
        ),
        "sensor.ext_limit": SimpleNamespace(
            state="90", attributes={"unit_of_measurement": "%"},
            last_reported=stamp, last_updated=stamp
        ),
    }
    monkeypatch.setattr(
        module.er, "async_get",
        lambda _: SimpleNamespace(entities=items, async_get=items.get)
    )
    return SimpleNamespace(states=SimpleNamespace(get=states.get)), items, states


def test_dye_percent_rating_from_exact_owned_device_sensor(monkeypatch):
    hass, items, states = setup_fake(monkeypatch)
    assert module.device_rated_w(hass, "number.inv") == 2000
    states["sensor.device"].attributes["Rated Power"] = 800
    assert module.device_rated_w(hass, "number.inv") == 800
    items["sensor.device"].device_id = "different-inverter"
    assert module.device_rated_w(hass, "number.inv") is None


@pytest.mark.parametrize("invalid", [None, 0, -1, 5001, float("nan"), True])
def test_unknown_or_invalid_dye_rating_never_guess(monkeypatch, invalid):
    hass, _, _ = setup_fake(monkeypatch, rated=invalid)
    assert module.device_rated_w(hass, "number.inv") is None


def test_bk_2150_per_fresh_identified_head_and_extension(monkeypatch):
    hass, _, states = setup_fake(monkeypatch)
    assert module.suggested_bk_capacity_wh(hass, "sensor.bk") == 4300
    states["sensor.ext"].state = "unavailable"
    assert module.suggested_bk_capacity_wh(hass, "sensor.bk") is None
    states["sensor.ext"].state = "75"
    states["sensor.ext"].last_reported = datetime.now(UTC) - timedelta(seconds=180)
    assert module.suggested_bk_capacity_wh(hass, "sensor.bk") is None


def test_head_only_bk_has_2150_while_other_vendors_remain_manual(monkeypatch):
    hass, _, _ = setup_fake(monkeypatch, expansion=False)
    assert module.suggested_bk_capacity_wh(hass, "sensor.bk") == 2150
    assert module.suggested_bk_capacity_wh(hass, "sensor.unrelated") is None


def test_legacy_export_mode_maps_without_accidentally_enabling_feed_in():
    assert configured_export_mode({"feed_in": False, "feed_in_mode": "pv"}) == FEED_IN_MODE_HOUSEHOLD
    assert configured_export_mode({"feed_in": True, "feed_in_mode": "household"}) == FEED_IN_MODE_HOUSEHOLD
    assert configured_export_mode({"feed_in": True}) == FEED_IN_MODE_PV
    assert configured_export_mode({}) == FEED_IN_MODE_HOUSEHOLD
