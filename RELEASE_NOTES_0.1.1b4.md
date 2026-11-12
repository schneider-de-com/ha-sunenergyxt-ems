# SunEnergyXT EMS v0.1.1b4 — experimental forecast/learning beta

Includes the reviewed forecast contracts, Solcast adapter, demand history and
passive HA observation (#93–#97), and optional storage-priority planning (#100).
Planning is off by default. Existing EMS enable/feed-in/PID settings are retained;
the existing controller remains the sole hardware writer.

Learning starts with complete fresh grid plus independent inverter AC samples.
Missing/offline measurements create gaps, never zeros. Demand history uses HA
Core Store with checkpoint readback; unusable storage disables planning.
Planning additionally requires verified capacities, sufficient demand history and
appropriate per-storage PV allocation. An aggregate positive PV forecast cannot
be arbitrarily divided across multiple batteries. See
[planning limitations](docs/horizon-planning.md).

This is an experimental installation for inspection and learning. The internal
same-input cost/GPE comparison and full actual-effect ledger remain Sprint-2
work. Installing this build does **not** begin the seven-day acceptance window.
Modeled advantage/overflow is not measured savings or measured curtailment.
There is no active dynamic tariff mode or grid charging.

## Installation and rollback

Before installation, capture the existing config-entry settings, installation
version and operating state. Keep the published `v0.1.1b3` package available.
Install this exact prerelease through Marketplace/HACS and restart Home Assistant
to activate the code. Verify the loaded manifest reports `0.1.1b4`, the integration
and device/entity lifecycle, planning remains off, and existing control/feedback
status and observer persistence after restart. Do not infer physical AC output
from service acceptance or a reported setpoint.

If setup or control regresses, disable EMS through its existing safe shutdown
path and inspect per-inverter readback/actual AC. Re-download `v0.1.1b3` through
Marketplace/HACS and restart; preserve config-entry values and the separate
demand history. The prior version ignores the added planning options. Restore
only any deliberately changed entry settings; a full HA backup restore is not
the routine rollback for this package update. Unreachable hardware cannot be
declared stopped by software readback alone.

No stable/unattended hardware approval follows from CI or this prerelease.
