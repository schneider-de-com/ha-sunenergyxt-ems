# SunEnergyXT EMS v0.1.1b16 — AC sensor ownership check

This experimental prerelease packages the focused safety correction from
PR #123. It does not establish Sprint-2 or sustained hardware acceptance.

## Change and verification

For the supported Solarman `deye_micro_53.yaml` profile, AC grid voltage must
belong to the inverter's actual Home Assistant config entry as well as its
device and integration-owned unique ID. Reassigning the sensor to another
entry can retain its device and unique ID; that sensor now fails closed.
Reassigning it back restores eligibility under the existing safety gates.
No such mismatch has been asserted on the installed plant.

The correction in PR #123 has failing-before/passing-after unit and native
HA registry-reassignment tests. Its PR and main CI passed the full runtime
and native lifecycle suites on HA 2026.10.4 and 2026.11.1, plus the existing
lint, type, security, negative-control, Hassfest, HACS and CodeQL gates.
This packaging revision must pass those gates before publication as well.
There are no further controller, option, storage-schema or hardware-writer
changes. Software tests do not prove physical AC response.

## Operational boundaries

The AC-voltage diagnostic uses HA report times, not independently verified
device acquisition times, and is not a safety-rated disconnection device.
It does not resolve the separate measured household-load source contract.
Missing or untrusted forecast/measurement data still select the existing
household-load fallback. No seven-day planning acceptance window has begun.

The user's regular b15 night run and its read-only evaluation are separate
from this release. Publishing b16 does not install it, interrupt that run,
enable control or authorize a second writer. Short reported export transients
are assessed by duration, height, settling and recovery; this release adds
no export trip threshold or new watchdog.

## Controlled update and rollback

Coordinate the update with any ongoing hardware run. Preserve options,
enabled intent and capture stores; disable EMS through its existing shutdown
path and verify outstanding minimum/acknowledgement duties before installing.
If required, perform one controlled HA restart, verify the actual loaded
version, unchanged options, retained records and explicit observation gaps.
Do not infer physical zero output from a service success or an actuator limit.
Resume only after fresh source/safety checks under the existing operating
authorization; installation success alone is no hardware acceptance.

The previous exact published release is `v0.1.1b15`, targeting
`58587f3ed2deb96cdd9f517133c5095ad77e09df`. Its existing AC-loss gate is
retained, but it lacks the actual-config-entry check added by PR #123.
Keep EMS disabled during rollback, verify the restored version and source
ownership, preserve journals/options and inspect shutdown/reconnect duties
before a controlled resumption. Never edit HA `.storage/` by hand. Do not
rewrite the published b15 tag.
