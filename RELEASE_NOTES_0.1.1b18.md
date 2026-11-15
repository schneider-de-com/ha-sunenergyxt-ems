# SunEnergyXT EMS v0.1.1b18 — Confirmed proportional actuator ramp

Experimental beta, implementing the software part of issue #68 in PR #127.
It does not certify inverter physical AC response, zero-export performance,
seven-day planning acceptance, or unattended operation.

## What changes

The **positive power-limit dispatch** now uses a shared proportional increase
budget after **three distinct positive signed-grid samples**. The budget is
the smaller of 200 W and 20% of the *minimum* of those three samples, with a
20 W floor. Illustrative budgets: +100 W import → 20 W; +250 W → 50 W;
+500 W → 100 W; +1,000 W and above → 200 W. The budget is shared by the
M200 and M80 for one dispatch, not issued separately to both inverters.

Before those samples exist, the former 20 W **per-inverter** fallback applies.
Fine 0.1-percent startup precision, 30-second startup hold, post-command fresh
Shelly and device acknowledgements (Deye/Solarman register 53), existing
5/20-second minimum intervals, and emergency reductions remain in force.
Coarse-percent devices now **round down** to a representable safe increment
rather than skip an authorized increase; a rounded readback may only unlock
further increases after real hardware-register confirmation.

The PID controller still computes desired power; these limits only constrain
which increase is actually sent. They do not prove physical AC ramp limits:
a positive inverter percentage or a BK215 DC measurement is not the same
as fresh synchronized inverter AC output.

## Verification and safety

PR #127 added failing-first regression tests for the 20/50/100/200 W response,
shared budgeting, distinct sample requirement, one-sample spikes, reductions,
coarse actuator rounding, missing feedback and missing-meter fallback.
Existing restart/reconnect, delayed-ACK, dual-inverter and emergency controls
were retained; live-derived control/overshoot scenarios and HA 2026.10.4 and
2026.11.1 CI suites must pass before this release is published.

This is a **software acceptance** scope. A supervised real-Hardware ramp
check under sustained high demand and a large-load drop remains required.
Short signed-grid export transients should be measured for duration and
settling time, not automatically treated as a test-failing millisecond trip.
See [issue #68](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/68).

## Install and fallback

Preserve the enabled/disabled intent and a recovery point, disable EMS via
its existing shutdown path, verify both inverter limits and pending shutdown
duties, then install the exact published beta and restart HA Core to load it.
After checking the loaded integration version, both device feedback paths,
signed-grid measurement and SoC reserves, resume the original enabled intent.

The prior published beta is `v0.1.1b17`; to roll back install that exact tag
while EMS is disabled, restart HA and verify readbacks before resuming.
A Proxmox backup reported as completed despite a failed HA Supervisor
fs-freeze/fs-thaw hook is not proof of an application-consistent restore.
