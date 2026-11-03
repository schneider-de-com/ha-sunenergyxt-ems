"""Behavior-first contracts for five requested JPS-inspired user features.

These tests intentionally exercise the pure policy before Home Assistant wiring.
"""

from custom_components.sunenergyxt_ems import logic


def pack(name: str, soc: float = 80, **extras: object) -> logic.Battery:
    return logic.Battery(
        key=name,
        soc=soc,
        lower_soc=10,
        upper_soc=90,
        inverters=(logic.Inverter(f"{name}_inverter", 800),),
        **extras,
    )


def test_adaptive_pid_has_a_bounded_integral_and_inspectable_terms() -> None:
    controller = logic.Controller(logic.ControllerState(outputs={"a_inverter": 400}))
    controller.adaptive_pid = True
    controller.update((pack("a"),), 180, 10, sequence=10)
    assert controller.state.pid_p_w == 80
    assert controller.state.pid_i_w == 0
    controller.update((pack("a"),), 180, 15, sequence=15)
    assert controller.state.pid_i_w > 0
    assert controller.state.pid_error_w == 160
    # Repeated identical timestamps must not accumulate integral repeatedly.
    before = controller.state.pid_i_w
    controller.update((pack("a"),), 180, 20, sequence=15)
    assert controller.state.pid_i_w == before
    for second in range(25, 1000, 5):
        controller.update((pack("a"),), 3000, second, sequence=second)
    assert abs(controller.state.pid_i_w) <= 240


def test_startup_hold_blocks_only_normal_changes_and_resets() -> None:
    hold = logic.StartHold(seconds=30)
    hold.record("m200", before_w=0, after_w=200, now=10)
    assert hold.active("m200", 25)
    assert hold.remaining("m200", 25) == 15
    assert not hold.active("m200", 40)
    hold.record("m200", before_w=200, after_w=230, now=41)
    assert not hold.active("m200", 42)
    hold.record("m200", before_w=0, after_w=200, now=50)
    hold.reset()
    assert not hold.active("m200", 51)


def test_after_full_charge_pv_input_limits_bypass_when_configured() -> None:
    controller = logic.Controller()
    controller.feed_in = True
    capped = pack("a", soc=90, pv_input_w=135, pv_input_configured=True)
    assert controller.update((capped,), 0, 1)["a_inverter"] == 135
    # Legacy setups without an input sensor keep their documented full-power semantics.
    assert controller.update((pack("a", soc=90),), 0, 2)["a_inverter"] == 800
    assert controller.update((pack("a", soc=87, pv_input_w=135),), -500, 3)["a_inverter"] == 280


def test_ev3600_charging_blocks_only_its_bk215_and_recovers() -> None:
    controller = logic.Controller()
    charging = pack("a", ev3600_charging=True)
    eligible = pack("b", soc=80)
    outputs = controller.update((charging, eligible), 500, 1)
    assert outputs["a_inverter"] == 0
    assert outputs["b_inverter"] > 0
    assert "a" in controller.state.ev3600_active
    # A configured but unverifiable EV measurement must not enable discharge.
    outputs = controller.update((pack("a", ev3600_charging=None), eligible), 500, 2)
    assert outputs["a_inverter"] == 0
    assert "a" in controller.state.ev3600_unknown
    outputs = controller.update((pack("a", ev3600_charging=False), eligible), 3000, 3)
    assert outputs["a_inverter"] > 0
    assert "a" not in controller.state.ev3600_active
    assert "a" not in controller.state.ev3600_unknown


def test_diagnostics_distinguish_pid_terms_from_measured_output() -> None:
    controller = logic.Controller()
    controller.update((pack("a"),), 450, 10, sequence=10)
    state = controller.state
    assert state.raw_grid_w == 450
    assert state.pid_error_w == 430
    assert state.target_w == controller.state.outputs["a_inverter"]
    assert state.pid_p_w > 0
    assert state.pid_i_w == 0
    assert isinstance(state.pid_d_w, float)

def test_pid_does_not_integrate_while_an_inverter_is_starting() -> None:
    """The 30-second startup hold must not silently build an integral backlog."""
    controller = logic.Controller(logic.ControllerState(outputs={"a_inverter": 200}))
    controller.adaptive_pid = True
    controller.pid_hold = True
    for tick in range(10, 40, 5):
        controller.update((pack("a"),), 550, tick, sequence=tick)
        assert controller.state.pid_i_w == 0
    controller.pid_hold = False
    controller.update((pack("a"),), 100, 45, sequence=45)
    assert controller.state.pid_i_w > 0


def test_ev3600_charging_clears_stale_full_charge_bypass_state() -> None:
    controller = logic.Controller()
    controller.feed_in = True
    controller.update((pack("a", soc=90),), 0, 1)
    assert "a" in controller.state.full
    controller.update((pack("a", soc=90, ev3600_charging=True),), 200, 2)
    assert "a" not in controller.state.full
    assert "a" in controller.state.ev3600_active

def test_derivative_is_damped_but_not_accidentally_clamped_to_fraction_of_a_watt() -> None:
    """Real 5-second meter changes should produce a measurable bounded D term."""
    from custom_components.sunenergyxt_ems.pid import PIDGains, bounded_pid

    result = bounded_pid(
        error=120.0,
        previous_error=20.0,
        dt=5.0,
        integral=0.0,
        current_w=400.0,
        maximum_w=800.0,
        gains=PIDGains(kd=0.12),
    )
    assert 1.0 <= result.derivative <= 120.0
