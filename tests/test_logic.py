"""Regression tests for the EMS controller, written before the policy."""

from custom_components.sunenergyxt_ems.logic import (
    Battery,
    Controller,
    ControllerState,
    Inverter,
    policy,
)


def battery(name="a", soc=70, low=10, inverters=None, charging=0):
    return Battery(
        name,
        soc,
        low,
        90,
        inverters or (Inverter(name + "_inv", 800),),
        charging,
    )


def test_missing_soc_only_blocks_that_battery():
    batteries = (battery("a", None), battery("b"))
    outputs, _ = policy(batteries, 500, ControllerState(), 1)
    assert outputs["a_inv"] == 0
    assert outputs["b_inv"] > 0


def test_low_soc_hysteresis():
    engine = Controller()
    assert engine.update((battery(soc=10),), 500, 1)["a_inv"] == 0
    assert engine.update((battery(soc=14),), 500, 2)["a_inv"] == 0
    assert engine.update((battery(soc=15),), 500, 3)["a_inv"] > 0


def test_offline_first_of_two_inverters_keeps_second():
    b = battery(
        inverters=(
            Inverter("a_inv", 800, online=False),
            Inverter("b_inv", 800),
        )
    )
    outputs, _ = policy((b,), 500, ControllerState(), 1)
    assert "a_inv" not in outputs
    assert outputs["b_inv"] > 0


def test_combined_output_respects_battery_limit():
    b = Battery("a", 80, 10, 90, (Inverter("a_inv", 1600), Inverter("b_inv", 1600)))
    outputs, _ = policy((b,), 4000, ControllerState(), 1)
    assert sum(outputs.values()) <= 1920


def test_grid_failure_holds_then_reduces_to_safe_minimum():
    engine = Controller()
    engine.update((battery(),), 500, 1)
    assert engine.update((battery(),), None, 3)["a_inv"] > 0
    assert engine.update((battery(),), None, 20)["a_inv"] == 0


def test_grid_recovers_only_after_three_fresh_samples():
    engine = Controller()
    b = (battery(),)
    engine.update(b, 500, 1)
    engine.update(b, None, 20)
    assert engine.update(b, 500, 21, sequence=1)["a_inv"] == 0
    assert engine.update(b, 500, 22, sequence=1)["a_inv"] == 0
    assert engine.update(b, 500, 23, sequence=2)["a_inv"] == 0
    assert engine.update(b, 500, 24, sequence=3)["a_inv"] > 0


def test_manual_disable_sets_safe_limit():
    engine = Controller()
    b = (battery(),)
    engine.update(b, 500, 1)
    assert engine.disable(b)["a_inv"] == 0
    assert engine.update(b, 500, 2)["a_inv"] == 0


def test_main_battery_is_selected_by_usable_soc():
    engine = Controller()
    b = (battery("a", 80), battery("b", 25))
    engine.update(b, 400, 1)
    assert engine.state.main == "a"
    assert engine.update(b, 400, 2)["a_inv"] > 0


def test_feed_in_requires_explicit_activation():
    engine = Controller()
    b = (battery(soc=90),)
    assert engine.update(b, 0, 1)["a_inv"] == 0
    engine.feed_in = True
    assert engine.update(b, 0, 2)["a_inv"] > 0
    assert engine.update((battery(soc=87),), -800, 3)["a_inv"] == 0


def test_equal_area_scores_preserve_setup_order():
    """Do not switch the main BK215 only because its name sorts differently."""
    engine = Controller()
    setup_order = (battery("a", 70), battery("z", 70))
    engine.update(setup_order, 500, 1)
    assert engine.state.main == "a"


def test_near_full_input_can_prefer_charging_battery():
    """PV input near the top SoC should influence main-area selection."""
    engine = Controller()
    batteries = (
        battery("a", 80, charging=0),
        battery("b", 80, charging=60),
    )
    engine.update(batteries, 500, 1)
    assert engine.state.main == "b"


def test_import_gain_uses_half_error_from_confirmed_limit():
    """Match the previously agreed EMS rule: 50% gain for grid import."""
    engine = Controller(ControllerState(outputs={"a_inv": 400}))
    result = engine.update((battery(),), 180, 1)
    assert result["a_inv"] == 480


def test_export_gain_uses_full_error_up_to_step_cap():
    """React faster to export than import, without unlimited corrections."""
    engine = Controller(ControllerState(outputs={"a_inv": 400}))
    result = engine.update((battery(),), -200, 1)
    assert result["a_inv"] == 180


def test_zero_feed_in_deadband_holds_previous_confirmed_limit():
    """A 25 W net import lies inside the explicitly agreed 0..30 W band."""
    engine = Controller(ControllerState(outputs={"a_inv": 400}))
    result = engine.update((battery(),), 25, 1)
    assert result["a_inv"] == 400


def test_large_import_step_is_capped_at_520_w():
    """One correction cannot leap from 0 to 1,180 W at a large load step."""
    b = Battery("a", 80, 10, 90, (Inverter("a_inv", 2000),))
    engine = Controller()
    result = engine.update((b,), 1200, 1)
    assert result["a_inv"] == 520
