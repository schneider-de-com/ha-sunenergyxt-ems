# SunEnergyXT EMS v0.1.0b8

**Experimental prerelease. The PID changes are software-tested; unattended inverter control is not hardware-approved.**

## Controller fix

- A positive PID integral is discarded when signed grid import settles in the 0–30 W target band. Previously it survived that state and could cause an inverter output increase when export appeared.
- A direct reversal of the grid-control error also discards an integral accumulated in the opposite direction (import to export and export to import).
- The controller still preserves feedback-gated and rate-limited actuator writes, emergency reductions, two-BK215 handling, and safe M80 shutdown/reconnect behavior introduced in earlier betas.

## Evidence and testing

- [PR #59](https://github.com/schneider-de-com/ha-sunenergyxt-ems/pull/59) merged on 5 November 2026 (commit `75db579`).
- RED-first tests reproduced the positive-integral bug: a 200 W target incorrectly rose to 270 W on a fresh −30 W grid measurement.
- Four deterministic policy regressions now cover settled target, subsequent export, direct import-to-export and direct export-to-import transitions.
- GitHub unit tests, HA runtime tests, actual HA entry lifecycle, mutation checks, hassfest, HACS validation and CodeQL passed on the fix commit.

## Hardware status and update

- Version v0.1.0b7 was previously tested with a **supervised** M200 30-minute run. This is not proof that v0.1.0b8 has passed an energized hardware test.
- The cause of the severe phase-A load transitions remains unresolved (#27, #53). DC output safety interpretation (#31) and M80 shutdown hardware validation (#32) remain open.
- Select the tagged prerelease in HACS/Marketplace. The existing Home Assistant config entry and its disabled state are preserved; installing does not authorize unattended inverter operation.
