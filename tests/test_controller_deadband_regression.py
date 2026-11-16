"""Regression tests for the sole bounded adaptive-proportional control path.

The regulator has no integral term: deadband, sign reversals and non-default
grid targets must be safe without carrying corrections across meter reports.
"""

from custom_components.sunenergyxt_ems.logic import Battery, Controller, ControllerState, Inverter


def _battery() -> Battery:
    return Battery(
        key="bk215",
        soc=70,
        lower_soc=10,
        upper_soc=90,
        inverters=(Inverter("m200", rated_w=2000),),
    )


def test_deadband_holds_last_confirmed_limit() -> None:
    controller = Controller(ControllerState(outputs={"m200": 200.0}))
    limits = controller.update((_battery(),), grid_w=20.0, now=10.0, sequence="settled")
    assert limits["m200"] == 200.0
    assert controller.state.regulation_error_w == 0.0
    assert controller.state.regulation_correction_w == 0.0


def test_export_after_deadband_reduces_limit() -> None:
    controller = Controller(ControllerState(outputs={"m200": 200.0}))
    before = controller.update((_battery(),), 20.0, 10.0, sequence="settled")
    after = controller.update((_battery(),), -30.0, 15.0, sequence="export")
    assert after["m200"] < before["m200"]


def test_direct_import_to_export_reversal_reduces_limit() -> None:
    controller = Controller(ControllerState(outputs={"m200": 200.0}))
    limits = controller.update((_battery(),), -30.0, 10.0, sequence="export")
    assert limits["m200"] < 200.0


def test_direct_export_to_import_reversal_increases_limit() -> None:
    controller = Controller(ControllerState(outputs={"m200": 200.0}))
    limits = controller.update((_battery(),), 120.0, 10.0, sequence="import")
    assert limits["m200"] > 200.0


def test_custom_grid_target_does_not_freeze_at_old_default() -> None:
    controller = Controller(ControllerState(outputs={"m200": 200.0}))
    controller.grid_target_w = 80.0
    limits = controller.update((_battery(),), 20.0, 10.0, sequence="below_target")
    assert limits["m200"] < 200.0


def test_custom_grid_target_holds_within_shifted_deadband() -> None:
    controller = Controller(ControllerState(outputs={"m200": 200.0}))
    controller.grid_target_w = 80.0
    limits = controller.update((_battery(),), 75.0, 10.0, sequence="settled")
    assert limits["m200"] == 200.0
    assert controller.state.regulation_correction_w == 0.0
