"""Positive, negative, and implausible input cases for MVP safety behavior."""

from math import inf, nan

import pytest

from custom_components.sunenergyxt_ems.discovery import (
    Candidate,
    find_batteries,
    suggest_grid,
)
from custom_components.sunenergyxt_ems.logic import (
    Battery,
    Controller,
    Inverter,
)


def pack(
    key="a", soc=70, lower=10, upper=90,
    *, inverter=None, charging=0.0, max_w=1920,
):
    """Minimal BK215 fixture; tests never call real Home Assistant services."""
    return Battery(
        key, soc, lower, upper,
        (inverter or Inverter(f"{key}_inv", 800),),
        charging, max_w,
    )


# Positive: valid values and useful outcomes.

def test_positive_import_is_followed_with_small_grid_reserve():
    engine = Controller()
    assert engine.update((pack(),), 600, 1) == {"a_inv": 580}


def test_positive_export_causes_power_reduction():
    engine = Controller()
    engine.update((pack(),), 600, 1)
    assert engine.update((pack(),), -400, 2) == {"a_inv": 160}


def test_positive_two_inverters_share_bk215_limit():
    battery = Battery(
        "a", 70, 10, 90,
        (Inverter("inv1", 1500), Inverter("inv2", 1500)),
    )
    cmd = Controller().update((battery,), 3000, 1)
    assert cmd["inv1"] <= 1500
    assert cmd["inv2"] <= 1500
    assert sum(cmd.values()) == 1920


def test_positive_multiple_batteries_keep_operating_if_one_is_unavailable():
    cmd = Controller().update((pack("a", soc=None), pack("b")), 600, 1)
    assert cmd == {"a_inv": 0, "b_inv": 580}


def test_positive_full_charge_is_opt_in_and_has_soc_hysteresis():
    engine = Controller()
    assert engine.update((pack(soc=90),), 0, 1)["a_inv"] == 0
    engine.feed_in = True
    assert engine.update((pack(soc=90),), 0, 2)["a_inv"] == 800
    assert engine.update((pack(soc=89),), 0, 3)["a_inv"] == 800
    assert engine.update((pack(soc=88),), -800, 4)["a_inv"] == 0


def test_positive_zero_grid_reading_is_valid():
    engine = Controller()
    engine.update((pack(),), 100, 1)
    assert engine.update((pack(),), 0, 2)["a_inv"] == 60


# Negative: missing data, offline devices, and recovery logic.

@pytest.mark.parametrize("bad", [None, -1, 101, nan, inf, -inf])
def test_negative_invalid_soc_excludes_only_affected_battery(bad):
    cmd = Controller().update((pack("bad", soc=bad), pack("good")), 600, 1)
    assert cmd["bad_inv"] == 0
    assert cmd["good_inv"] > 0


@pytest.mark.parametrize("bad", [None, -1, 101, nan, inf, -inf])
def test_negative_invalid_discharge_limit_excludes_battery(bad):
    assert Controller().update((pack(lower=bad),), 600, 1) == {"a_inv": 0}


def test_negative_single_offline_inverter_excludes_battery():
    offline = pack(inverter=Inverter("inv", 800, online=False))
    assert Controller().update((offline,), 600, 1) == {}


def test_negative_one_offline_inverter_does_not_disable_its_partner():
    paired = Battery(
        "a", 70, 10, 90,
        (Inverter("offline", 800, online=False), Inverter("working", 800)),
    )
    assert Controller().update((paired,), 600, 1) == {"working": 580}


def test_negative_grid_loss_lowers_inverter_after_grace():
    engine = Controller()
    engine.update((pack(),), 600, 1)
    assert engine.update((pack(),), None, 4)["a_inv"] == 580
    assert engine.update((pack(),), None, 20)["a_inv"] == 0


def test_negative_recovery_needs_three_distinct_fresh_samples():
    engine = Controller()
    engine.update((pack(),), 600, 1)
    engine.update((pack(),), None, 20)
    for seq in (1, 1, 2, 2):
        assert engine.update((pack(),), 600, 21 + seq, sequence=seq)["a_inv"] == 0
    assert engine.update((pack(),), 600, 24, sequence=3)["a_inv"] > 0


def test_negative_manual_disable_lowers_output():
    engine = Controller()
    engine.update((pack(),), 600, 1)
    assert engine.disable((pack(),))["a_inv"] == 0
    assert engine.update((pack(),), 600, 2)["a_inv"] == 0


def test_negative_no_batteries_return_no_commands():
    assert Controller().update((), 600, 1) == {}


# Implausible: values a real entity should never provide. Fail closed.

@pytest.mark.parametrize("bad", [nan, inf, -inf])
def test_implausible_grid_meter_value_stops_control_after_grace(bad):
    engine = Controller()
    engine.update((pack(),), 600, 1)
    assert engine.update((pack(),), bad, 20)["a_inv"] == 0


@pytest.mark.parametrize("rated", [-100, nan, inf])
def test_implausible_inverter_rating_must_not_produce_bad_commands(rated):
    cmd = Controller().update((pack(inverter=Inverter("inv", rated)),), 600, 1)
    assert cmd == {}  # Invalid device configuration must not be controlled.


@pytest.mark.parametrize("minimum", [-10, nan, inf, 900])
def test_implausible_inverter_minimum_must_not_produce_bad_commands(minimum):
    cmd = Controller().update((pack(inverter=Inverter("inv", 800, minimum)),), 600, 1)
    assert cmd == {}  # Out-of-range minimum: refuse unsafe outputs.


@pytest.mark.parametrize("limit", [-50, nan, inf])
def test_implausible_battery_output_limit_excludes_it(limit):
    cmd = Controller().update((pack(max_w=limit),), 600, 1)
    assert cmd == {"a_inv": 0}  # A bad capacity must still trigger a safe minimum.


def test_implausible_backward_clock_does_not_extend_grid_hold():
    engine = Controller()
    engine.update((pack(),), 600, 100)
    assert engine.update((pack(),), None, 99)["a_inv"] == 0


def test_implausible_huge_grid_spike_is_not_treated_as_real_demand():
    engine = Controller()
    assert engine.update((pack(),), 1_000_000_000, 1)["a_inv"] == 0


def test_implausible_soc_in_wrong_sensor_unit_is_not_detected_as_bk215():
    items = [Candidate("sensor.soc", "sensor", "bk", "sunEnergyXT", "wifi_soc", "W")]
    assert find_batteries(items) == {}


def test_implausible_unrelated_power_sensor_is_not_selected_as_grid_meter():
    items = [Candidate("sensor.heater", "sensor", "heater", "shelly", "power", "W")]
    assert suggest_grid(items) is None

def test_negative_missing_soc_during_grid_grace_is_not_held_at_old_power():
    """Battery safety wins over the short grid-meter grace period."""
    engine = Controller()
    assert engine.update((pack(),), 600, 1)["a_inv"] > 0
    assert engine.update((pack(soc=None),), None, 3)["a_inv"] == 0


def test_positive_larger_configured_limit_still_respects_bk215_hardware_cap():
    battery = Battery(
        "a", 70, 10, 90,
        (Inverter("inv1", 1500), Inverter("inv2", 1500)),
        output_limit_w=4000,
    )
    outputs = Controller().update((battery,), 5000, 1)
    assert sum(outputs.values()) == 1920
