"""Future storage priority needs an explicit same-input advantage."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.sunenergyxt_ems.forecast import EnergyInterval, ForecastError
from custom_components.sunenergyxt_ems.logic import Battery, Controller, ControllerState, Inverter
from custom_components.sunenergyxt_ems.planning import plan_priority

NOW = datetime(2026, 11, 8, 18, tzinfo=UTC)


def forecast(watts):
    return tuple(
        EnergyInterval(
            NOW + timedelta(minutes=30 * i), NOW + timedelta(minutes=30 * (i + 1)), value / 2
        )
        for i, value in enumerate(watts)
    )


def systems():
    # Synthetic asymmetric stores: the stronger inverter has less energy.
    return (
        Battery("fast", 10 + 1500 / 2150 * 100, 10, 100, (Inverter("fast_inv", 2000),)),
        Battery("slow", 10 + 3000 / 4300 * 100, 10, 100, (Inverter("slow_inv", 800),)),
    )


def plan(load=None, pv=None, **kwargs):
    return plan_priority(
        systems(),
        {"fast": 2150, "slow": 4300},
        forecast([500] * 4 + [1500] * 4) if load is None else load,
        {"fast": forecast([0] * 8), "slow": forecast([0] * 8)} if pv is None else pv,
        state=ControllerState(main="fast", support="slow"),
        **kwargs,
    )


def test_peak_shift_with_less_terminal_energy_is_not_a_flat_price_saving():
    result = plan()
    assert result.order == ()
    assert result.reason == "no_expected_advantage"
    assert result.expected_advantage_wh == 0


def test_discharging_the_future_solar_receiver_avoids_expected_overflow():
    bats = (
        Battery("fast", 10 + 3300 / 4300 * 100, 10, 100, (Inverter("fast_inv", 2000),)),
        Battery("slow", 10 + 1700 / 2150 * 100, 10, 100, (Inverter("slow_inv", 800),)),
    )
    result = plan_priority(
        bats,
        {"fast": 4300, "slow": 2150},
        forecast([500] * 8),
        {"fast": forecast([0] * 4 + [1500] * 4), "slow": forecast([0] * 8)},
        state=ControllerState(main="slow", support="fast"),
    )
    assert result.order[0] == "fast"
    assert result.reason == "expected_pv_loss_reduction"
    assert result.expected_advantage_wh > 0
    assert result.planned.overflow_wh < result.baseline.overflow_wh
    assert result.planned.import_by_slot[0] <= result.baseline.import_by_slot[0]


def test_flat_price_merely_shifting_grid_import_does_not_authorize_change():
    result = plan(load=forecast([500] * 8))
    assert result.order == ()
    assert result.reason == "no_expected_advantage"
    assert result.expected_import_reduction_wh == 0


def test_missing_per_storage_solar_forecast_is_not_assumed_zero():
    with pytest.raises(ForecastError, match="pv_allocation_unavailable"):
        plan(pv={})


def test_replay_excludes_inverters_rejected_by_the_live_allocator():
    battery = Battery(
        "store",
        90,
        10,
        100,
        (Inverter("good", 400), Inverter("invalid", 800, minimum_w=900)),
    )
    result = plan_priority((battery,), {"store": 4300}, forecast([1000]), {"store": forecast([0])})
    assert result.baseline.import_wh == pytest.approx(300)
    assert result.order == ()


def test_misaligned_or_gapped_forecasts_cannot_authorize_priority():
    with pytest.raises(ForecastError):
        plan(load=forecast([500] * 8)[::2])
    pv = {"fast": forecast([0] * 7), "slow": forecast([0] * 8)}
    with pytest.raises(ForecastError):
        plan(pv=pv)


def test_simulation_has_no_grid_charging_or_energy_creation():
    result = plan()
    assert all(value >= 0 for value in result.planned.final_energy_wh.values())
    assert sum(result.planned.final_energy_wh.values()) <= 4500
    assert result.planned.import_wh == pytest.approx(sum(result.planned.import_by_slot))


def test_simulation_does_not_mutate_live_controller_state():
    state = ControllerState(main="fast", support="slow", outputs={"fast_inv": 200})
    before = repr(state)
    plan_priority(
        systems(),
        {"fast": 2150, "slow": 4300},
        forecast([500] * 8),
        {key: forecast([0] * 8) for key in ("fast", "slow")},
        state=state,
    )
    assert repr(state) == before


def test_controller_priority_hint_cannot_bypass_reserve_or_offline_limits():
    controller = Controller()
    controller.planning_order = ("slow", "fast")
    controller.planning_expires_at = 100
    fast, slow = systems()
    blocked = Battery("slow", 10, 10, 100, slow.inverters)
    outputs = controller.update((fast, blocked), 600, 1)
    assert outputs["slow_inv"] == 0
    assert outputs["fast_inv"] > 0


def test_controller_discards_expired_priority_hint():
    controller = Controller()
    controller.planning_order = ("slow", "fast")
    controller.planning_expires_at = 1
    controller.update(systems(), 600, 2)
    assert controller.planning_order == ()


def test_current_priority_reaches_the_existing_household_allocator():
    controller = Controller()
    controller.planning_order = ("slow", "fast")
    controller.planning_expires_at = 100
    outputs = controller.update(systems(), 600, 1)
    assert controller.state.main == "slow"
    assert outputs["slow_inv"] > 0
    assert outputs["fast_inv"] == 0
