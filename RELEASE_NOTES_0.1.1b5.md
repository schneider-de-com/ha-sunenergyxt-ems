# SunEnergyXT EMS v0.1.1b5 — experimental operating-evidence beta

Adds the internal same-input comparison from #102 and the separate real-operation
capture. The existing passive observer records minute estimates from eligible
grid/PV/DC/AC power, individual covered time, observed operating/planning states
and package version. Missing channels, restart gaps and unsaved crash tails are
not zero-filled. Snapshot/restore work uses executor workers; there is still one
hardware writer and no additional polling or timer.

See [capture semantics](docs/operating-evidence.md) and
[internal comparison limits](docs/internal-comparison.md). Integrated power is
not a certified energy counter, physical battery cycling or measured savings.
Model overflow is not measured curtailment. GPE prices remain internal
hypothetical billing inputs and never control hardware.

## Experimental installation

Capture existing options/current state first. Use the EMS's existing safe-off
path before restarting; inspect pending readbacks and independent physical
telemetry, without declaring AC zero from an optimistic setpoint. Download this
exact prerelease through Marketplace/HACS, restart HA, and verify loaded manifest
`0.1.1b5`, retained settings, observer persistence and source coverage. Exercise an
entry reload to verify retained evidence and its explicit gap. Check Settings →
Devices & Services → Integrations → SunEnergyXT EMS in the actual UI.

Planning stays off by default. Enabling it needs checked capacities and forecast
sources, sufficient learned load data, preserved safety gates and a verified
installed fallback. Installation alone does not start the seven-day window.
Record its separate start/installed commit only after planning and capture pass
their operating checks; earlier basis-controller or learning data do not count.

## Rollback

If needed, use the existing safe-off path and diagnose hardware readback/actual
power. Secure the verified b5 checkpoint in a protected Home Assistant backup
before returning to b4; do not publish that backup or its credentials. Then
re-download published `v0.1.1b4` and restart HA. Keep entry options and
the demand/operating store: b4 ignores the added operating evidence field. During
b4 operation that added field may be dropped by its older checkpoint format;
returning to b5 must therefore start a new evidenced period. Record rollback,
the missing period and the new capture start separately: b5 cannot automatically
reconstruct a deleted evidence field. Never claim continuous capture across it.
`v0.1.1b3` remains the earlier
controller-only package if required. Do not use a full HA backup restore as the
routine package rollback. No unattended stable hardware approval follows from
the prerelease or green CI.
