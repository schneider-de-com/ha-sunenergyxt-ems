# SunEnergyXT EMS v0.1.1b17 — Inverter availability diagnostics

This experimental prerelease packages PR #125 together with the AC sensor
ownership correction already published in b16 (PR #123). It adds no further
runtime changes and does not establish sustained hardware or Sprint-2 acceptance.

## Changes and software verification

Existing battery status sensors now explain each inverter's first failing
current HA availability check. See [the diagnostic contract](docs/inverter-availability.md).
These checks are reused by the controller; the release adds no eligibility
gate, device polling, hardware writer, export threshold or PID adjustment.
Reserve, DC, acknowledgement, manual-disable, shutdown and reconnect duties
remain in force. `available` is not permission to discharge or proof of AC output.
The diagnostic cannot retrospectively establish an outage's physical cause.

PR #125 records actual failing-before/passing-after tests, native HA reload
coverage and separate Standards/Spec reviews. Its head and main CI passed
runtime and native lifecycle suites on HA 2026.10.4 and 2026.11.1, plus the
existing lint, type, security, negative-control, Hassfest, HACS and CodeQL gates.
This version/documentation-only packaging revision must pass those gates too;
it does not introduce new behavior requiring another artificial RED test.

## Field and planning boundaries

Earlier b15 baseline operation and its read-only overnight evaluation remain
separate evidence. They do not validate b17 hardware behavior, measured savings,
complete household-load learning or seven days of forecast planning.
Missing or untrusted measurement/forecast data still select the existing
household-load fallback. No active dynamic tariff control, comparison dashboard
or new acceptance threshold is included. Short reported export transients
require assessment of height, duration, settling and recovery.

## Controlled update and rollback

Coordinate with ongoing operation. Verify a recovery backup/snapshot first;
preserve the current options, enabled intent and capture-store checkpoints.
Disable EMS through its existing shutdown path, verify outstanding minimum
and acknowledgement duties, and install the exact published tag. If required,
perform one controlled HA restart. Verify the actual loaded version, unchanged
options, retained records and explicit observation gaps. Check current sources
and safety gates before a supervised resumption of the original enabled intent.
An intentionally disabled entry stays disabled. A successful service call or
zero power-limit setting is not independently measured physical zero output.

The previous published package is `v0.1.1b16`, targeting
`f9d869833f7d7a3d48788bf78292429a82a0b8f2`; it contains the AC sensor ownership
check but lacks these availability attributes. A system updating directly from
`v0.1.1b15` has previous package revision
`58587f3ed2deb96cdd9f517133c5095ad77e09df`, which also lacks the ownership check.
Keep EMS disabled during rollback; verify the restored version, source ownership,
options, journals and shutdown/reconnect duties before controlled resumption.
Never edit HA `.storage/` by hand or rewrite a published tag.
