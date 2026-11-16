"""Priority-2 behavioral contracts: coordinated storage and adaptive grid reserve.

No hardware writes are performed by these pure policy tests.
"""

from custom_components.sunenergyxt_ems.logic import (
    Battery,
    Controller,
    ControllerState,
    GridMedianFilter,
    Inverter,
)


def tower(key: str, soc: float = 70, lower: float = 10) -> Battery:
    return Battery(
        key=key,
        soc=soc,
        lower_soc=lower,
        upper_soc=90,
        inverters=(Inverter(key + "_inverter", 800),),
    )


def test_sustained_import_can_share_power_before_first_head_is_saturated():
    one, two = tower("a"), tower("b")
    controller = Controller()
    # Before three fresh confirmed import observations, preserve the existing
    # safe main/support allocation.
    baseline = controller.update((one, two), 1000, 1)
    assert baseline["a_inverter"] == 490
    assert baseline["b_inverter"] == 0

    controller.cooperative_dispatch = True
    commands = controller.update((one, two), 1000, 2)
    assert 0 < commands["a_inverter"] < 800
    assert 0 < commands["b_inverter"] < 800
    assert sum(commands.values()) == 490
    assert controller.state.cooperative_active is True


def test_cooperation_weights_usable_soc_without_crossing_individual_reserve():
    controller = Controller()
    controller.cooperative_dispatch = True
    commands = controller.update((tower("near", 12), tower("healthy", 80)), 1000, 1)
    assert commands["healthy_inverter"] > commands["near_inverter"]
    assert sum(commands.values()) == 490
    # At reserve the affected head alone is blocked, and surviving output
    # can use the whole safely requested budget.
    commands = controller.update((tower("near", 10), tower("healthy", 80)), 1000, 2)
    assert commands["near_inverter"] == 0
    assert commands["healthy_inverter"] == 490


def test_cooperative_allocation_does_not_force_second_head_to_zero_at_setpoint():
    controller = Controller(
        ControllerState(outputs={"a_inverter": 240, "b_inverter": 240})
    )
    controller.cooperative_dispatch = True
    controller.update((tower("a"), tower("b")), 550, 1)
    # Sustained import criterion may end, but a healthy allocation must not
    # create another 0% restart just because the meter approached its goal.
    controller.cooperative_dispatch = False
    result = controller.update((tower("a"), tower("b")), 20, 2)
    assert result["a_inverter"] > 0
    assert result["b_inverter"] > 0
    assert sum(result.values()) == 740


def test_cooperative_allocation_abandons_unavailable_head_only():
    controller = Controller()
    controller.cooperative_dispatch = True
    a = tower("a")
    b = Battery("b", 70, 10, 90, (Inverter("b_inverter", 800, online=False),))
    commands = controller.update((a, b), 1000, 1)
    assert commands == {"a_inverter": 490}
    assert controller.state.cooperative_active is False


def test_dynamic_import_buffer_needs_five_distinct_near_goal_samples():
    filt = GridMedianFilter()
    for index, watts in enumerate((20, -60, 45, -35)):
        filt.observe(watts, index)
    assert filt.dynamic_target(20) == 20
    filt.observe(40, 4)
    assert 20 < filt.dynamic_target(20) <= 200
    # The configured target is the lower bound, never silently lowered.
    assert filt.dynamic_target(100) >= 100
    filt.reset()
    assert filt.dynamic_target(20) == 20


def test_dynamic_buffer_never_uses_distant_import_spikes_or_duplicate_meter_samples():
    filt = GridMedianFilter()
    for index, watts in enumerate((1400, 1800, 2200, 1600, 1700)):
        filt.observe(watts, index)
    assert filt.dynamic_target(20) == 20
    filt.reset()
    for watts in (25, -40, 25, -40, 25):
        filt.observe(watts, "same-report")
    assert filt.dynamic_target(20) == 20


def test_strongly_falling_confirmed_import_slowdown_guards_positive_ramp():
    filt = GridMedianFilter()
    for sequence, watts in enumerate((1500, 1100, 700)):
        filt.observe(watts, sequence)
    assert filt.sustained_import()
    assert filt.rapid_import_decline()
    filt.reset()
    for sequence, watts in enumerate((1500, 1480, 1470)):
        filt.observe(watts, sequence)
    assert not filt.rapid_import_decline()
