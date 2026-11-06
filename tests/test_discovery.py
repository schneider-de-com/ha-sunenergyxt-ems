"""Registry matching must not rely on user-changeable entity IDs."""

from custom_components.sunenergyxt_ems.discovery import (
    Candidate,
    find_batteries,
    suggest_grid,
    suggest_inverter_limit,
)


def test_battery_roles_use_device_and_source_keys():
    entities = [
        Candidate("sensor.renamed_soc", "sensor", "dev_a", "sunEnergyXT", "wifi_soc", "%"),
        Candidate(
            "number.discharge", "number", "dev_a", "sunEnergyXT", "wifi_cnfg_disc_soc_min", "%"
        ),
        Candidate("number.charge", "number", "dev_a", "sunEnergyXT", "wifi_cnfg_chg_soc_max", "%"),
        Candidate("sensor.not_a_battery", "sensor", "dev_b", "other", "wifi_soc", "%"),
    ]
    assert find_batteries(entities) == {
        "dev_a": {
            "soc": "sensor.renamed_soc",
            "lower": "number.discharge",
            "upper": "number.charge",
        }
    }


def test_new_official_bk_domain_discovers_battery_roles():
    entities = [
        Candidate("sensor.soc", "sensor", "bk", "sunenergyxt_bk", "wifi_soc", "%"),
        Candidate(
            "number.reserve", "number", "bk", "sunenergyxt_bk", "wifi_cnfg_disc_soc_min", "%"
        ),
        Candidate("sensor.output", "sensor", "bk", "sunenergyxt_bk", "wifi_outputpower_total", "W"),
    ]
    assert find_batteries(entities) == {
        "bk": {"soc": "sensor.soc", "lower": "number.reserve", "dc_output": "sensor.output"}
    }


def test_inverter_suggestion_is_only_a_suggestion():
    items = [
        Candidate(
            "number.some_limit", "number", "inverter_a", "solarman", "active_power_regulation", "%"
        )
    ]
    assert suggest_inverter_limit(items, "inverter_a") == "number.some_limit"
    assert suggest_inverter_limit(items, "unknown") is None


def test_grid_uses_power_not_cumulative_energy():
    entities = [
        Candidate(
            "sensor.grid_total_kwh", "sensor", "meter_a", "shelly", "total_active_energy", "kWh"
        ),
        Candidate("sensor.grid_power", "sensor", "meter_a", "shelly", "total_active_power", "W"),
    ]
    assert suggest_grid(entities) == "sensor.grid_power"


def test_battery_discovery_survives_temporary_missing_state_units():
    """The configured BK215 must remain selectable even while disconnected."""
    entries = [
        Candidate("sensor.arbitrary_soc", "sensor", "bk_a", "sunEnergyXT", "wifi_soc", None),
        Candidate(
            "number.arbitrary_limit",
            "number",
            "bk_a",
            "sunEnergyXT",
            "wifi_cnfg_disc_soc_min",
            None,
        ),
    ]
    assert find_batteries(entries) == {
        "bk_a": {
            "soc": "sensor.arbitrary_soc",
            "lower": "number.arbitrary_limit",
        }
    }


def test_multiple_grid_meters_are_not_selected_automatically():
    entries = [
        Candidate("sensor.one", "sensor", "a", "energy", "net_power", "W"),
        Candidate("sensor.two", "sensor", "b", "energy", "net_power", "W"),
    ]
    assert suggest_grid(entries) is None


def test_bk215_t34_dc_output_is_discovered_in_watts():
    entries = [
        Candidate("sensor.soc", "sensor", "bk", "sunEnergyXT", "wifi_soc", "%"),
        Candidate("sensor.output", "sensor", "bk", "sunEnergyXT", "wifi_outputpower_total", "W"),
    ]
    assert find_batteries(entries)["bk"]["dc_output"] == "sensor.output"


def test_invalid_dc_unit_is_not_treated_as_watts():
    entries = [
        Candidate("sensor.soc", "sensor", "bk", "sunEnergyXT", "wifi_soc", "%"),
        Candidate("sensor.output", "sensor", "bk", "sunEnergyXT", "wifi_outputpower_total", "kWh"),
    ]
    assert "dc_output" not in find_batteries(entries)["bk"]
