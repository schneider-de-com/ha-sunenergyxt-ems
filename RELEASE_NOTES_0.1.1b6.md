# SunEnergyXT EMS v0.1.1b6 — experimental evidence-summary beta

Includes #104: internal diagnostics aggregate all retained channel energy
estimates, covered seconds and observed state durations, beyond the five-minute
preview. The scan runs off the control event loop under the existing observer
lock. A verified checkpoint timestamp is separate from a potentially
uncheckpointed tail. Partial measurements are not extrapolated to a complete
day or acceptance window. No controller, source mapping or polling policy is
changed; no active dynamic tariff mode or comparison dashboard is added.

Installation requires a checked device state. Do not activate EMS, change its
meter mapping or clear shutdown obligations while a configured inverter remains
unreachable with an unconfirmed shutdown. Package availability and green CI are
not activation approval. Preserve the existing reserve/DC/reconnect gates.

Once the installation's actual shutdown/readback state is resolved, capture
options and install this exact prerelease via Marketplace/HACS. Restart HA and
verify the loaded manifest, retained settings and measured-evidence checkpoint.
Check real entry reload, source coverage and the actual integration UI. Keep
planning off until its sources/capacities/fallback and hardware gates are checked.
Record a seven-day acceptance start separately; none begins automatically.

Rollback: use the existing safe-off path, preserve the verified evidence and
settings, then re-download `v0.1.1b5` and restart. B5 uses the same capture format
but has only the short diagnostic preview. Earlier b4 can drop the capture field;
follow the protected-checkpoint and explicit-gap instructions in
[b5 release notes](RELEASE_NOTES_0.1.1b5.md) if that older package is required.
Never delete evidence or bypass a safety gate to make installation appear green.

See [capture semantics](docs/operating-evidence.md) and
[internal comparison limits](docs/internal-comparison.md). This remains
experimental software without stable unattended hardware approval.
