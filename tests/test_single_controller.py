"""RED-first contract for the single adaptive proportional EMS regulation path.

The integration is pre-stable; old experimental PID flags must not influence
the live regulation decisions, state or Home Assistant options surface.
"""

from custom_components.sunenergyxt_ems.logic import Battery, Controller, ControllerState, Inverter


def _battery(name: str = "bk215") -> Battery:
    return Battery(
        key=name,
        soc=70.0,
        lower_soc=10.0,
        upper_soc=90.0,
        inverters=(Inverter(f"{name}_inverter", rated_w=2000),),
    )


def test_only_one_controller_algorithm_exists():
    controller = Controller()
    assert not hasattr(controller, "adaptive_pid")
    assert not hasattr(controller, "pid_gains")
    assert not hasattr(controller, "pid_hold")
    assert not hasattr(controller.state, "pid_i_w")


def test_single_path_import_export_and_signed_target():
    b = _battery()
    controller = Controller(ControllerState(outputs={"bk215_inverter": 200.0}))
    controller.grid_target_w = 80.0
    # 20 W positive import is still below the 80 W configured target.
    low_import = controller.update((b,), 20.0, now=1.0, sequence="low_import")
    assert low_import["bk215_inverter"] < 200.0
    before = low_import["bk215_inverter"]
    export = controller.update((b,), -60.0, now=2.0, sequence="export")
    assert export["bk215_inverter"] < before
    back_to_import = controller.update((b,), 350.0, now=3.0, sequence="import")
    assert back_to_import["bk215_inverter"] > export["bk215_inverter"]


def test_near_target_never_accumulates_integral_or_writes_new_limit():
    b = _battery()
    controller = Controller(ControllerState(outputs={"bk215_inverter": 200.0}))
    for now in range(1, 60):
        result = controller.update((b,), grid_w=20.0, now=float(now), sequence=now)
        assert result["bk215_inverter"] == 200.0
    assert not hasattr(controller.state, "pid_i_w")


def test_disabled_single_controller_stops_without_algorithm_fallback():
    b = _battery()
    controller = Controller(ControllerState(outputs={"bk215_inverter": 300.0}))
    assert controller.disable((b,))["bk215_inverter"] == 0.0
    assert controller.update((b,), 500.0, now=12.0, sequence="new")["bk215_inverter"] == 0.0
