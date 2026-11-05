"""Regression: a settled grid must not retain a previous positive PID integral.

A stale integrator can outweigh the negative proportional correction when the
Shelly changes from the import deadband to export. Test the *direction* of the
next command, not only the absence of a command while inside the deadband.
"""

from custom_components.sunenergyxt_ems.logic import (
    Battery,
    Controller,
    ControllerState,
    Inverter,
)


def test_grid_deadband_clears_previous_positive_pid_integral() -> None:
    battery = Battery(
        key="bk215",
        soc=70,
        lower_soc=10,
        upper_soc=90,
        inverters=(Inverter("m200", rated_w=2000),),
    )
    controller = Controller(ControllerState(outputs={"m200": 200.0}, pid_i_w=120.0))
    controller.adaptive_pid = True

    limits = controller.update((battery,), grid_w=20.0, now=10.0, sequence="at-target")

    assert limits["m200"] == 200.0
    assert controller.state.pid_i_w == 0.0, (
        "An old positive integral must not survive a settled 0..30 W grid reading"
    )


def test_export_after_deadband_never_increases_m200_due_to_stale_integral() -> None:
    battery = Battery(
        key="bk215",
        soc=70,
        lower_soc=10,
        upper_soc=90,
        inverters=(Inverter("m200", rated_w=2000),),
    )
    controller = Controller(ControllerState(outputs={"m200": 200.0}, pid_i_w=120.0))
    controller.adaptive_pid = True
    before = controller.update((battery,), grid_w=20.0, now=10.0, sequence="at-target")

    after = controller.update((battery,), grid_w=-30.0, now=15.0, sequence="export")

    assert after["m200"] < before["m200"], (
        "A fresh export sample must reduce the requested power, not increase it"
    )


def test_direct_import_to_export_transition_discards_old_positive_integral() -> None:
    """A sign reversal may happen without an intermediate deadband sample."""
    battery = Battery(
        key="bk215",
        soc=70,
        lower_soc=10,
        upper_soc=90,
        inverters=(Inverter("m200", rated_w=2000),),
    )
    controller = Controller(
        ControllerState(
            outputs={"m200": 200.0},
            pid_i_w=120.0,
            pid_last_error_w=100.0,
            pid_last_at=5.0,
            pid_last_sequence="import",
        )
    )
    controller.adaptive_pid = True

    limits = controller.update((battery,), grid_w=-30.0, now=10.0, sequence="export")

    assert limits["m200"] < 200.0, (
        "PID must not increase inverter output after a fresh import-to-export reversal"
    )


def test_direct_export_to_import_transition_discards_old_negative_integral() -> None:
    """Returning to import must not keep the old export reduction bias."""
    battery = Battery(
        key="bk215",
        soc=70,
        lower_soc=10,
        upper_soc=90,
        inverters=(Inverter("m200", rated_w=2000),),
    )
    controller = Controller(
        ControllerState(
            outputs={"m200": 200.0},
            pid_i_w=-120.0,
            pid_last_error_w=-50.0,
            pid_last_at=5.0,
            pid_last_sequence="export",
        )
    )
    controller.adaptive_pid = True

    limits = controller.update((battery,), grid_w=120.0, now=10.0, sequence="import")

    assert limits["m200"] > 200.0, (
        "PID must not keep lowering inverter output when fresh grid import has returned"
    )
