# SunEnergyXT EMS v0.1.1b15 — Deye AC-loss guard and full HA 2026.11 validation

This experimental prerelease contains the MVP AC-loss protection from PR #120,
the HA 2026.10.4/2026.11.1 runtime and real lifecycle test matrix from PR #121,
and a replay based on the 2026-11-14 real Home Assistant Recorder incident.

## Change

- Recognize the supported Solarman `deye_micro_53.yaml` Deye profile and find
  its AC-grid-voltage source from the SAME device, entry and integration-owned
  unique ID, never from user-editable entity names. Unsupported profiles
  preserve their existing behavior.
- Missing, stale, zero or implausible grid voltage blocks normal positive
  dispatches to that Deye; the EMS attempts an immediate minimum command.
  Healthy independent BK215 systems remain eligible. After voltage recovery
  the interlock requires a new minimum/acknowledgement before positive output.
- Regressions include the observed 2026-11-14 dual-Deye voltage drop from
  226/224 V to 0 V within one second; HA-reported positive number limits
  continued while Solarman communication was live. These are inverter LIMIT
  readings, not measured physical AC power.
- Run the full HA runtime and real entry/registry/lifecycle suites on
  Home Assistant 2026.10.4 AND 2026.11.1, as well as ordinary policy tests,
  pessimistic negative tests, Ruff, Hassfest, HACS and CodeQL.

## Boundaries and release criteria

The voltage diagnostic has no independently verified *device acquisition*
timestamp, and is not a safety-rated disconnection device. Backtest replay
is counterfactual software logic only; no actual AC response, energy savings
or stable zero-export rate is proven by the fixture. The MVP physical
acceptance still requires sustained real closed-loop measurements,
the M200 0→1% transient test, shutdown/reconnect tests, and a genuine
full-charge bypass test with reachable upper SoC. A 24-hour controlled
hardware acceptance has not been shown.

No storage schema or existing option names are changed. Sprint-2 planning
is not part of the original MVP physical acceptance.

## Installation and rollback

Before upgrading, preserve the current EMS options and enabled intent,
confirm the legacy five-second automation is OFF, and inspect AC voltage,
BK215 DC, Solarman limits and available Proxmox backup. Deploy the
tagged prerelease with Marketplace/HACS and restart Home Assistant once if
required, without automatically enabling EMS. Verify the loaded version,
clean shutdown journal and zero actuator limits before any supervised trial.

The previous exact published release is `v0.1.1b14`. It has NO AC-loss
guard. On rollback, keep the EMS disabled. Never edit HA `.storage/` by hand.
