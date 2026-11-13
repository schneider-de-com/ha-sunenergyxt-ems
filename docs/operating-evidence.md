# Real-operation evidence

The existing passive five-second observer also captures independently eligible
cached power channels: signed grid power, selected per-battery PV input and DC
output, and selected independent inverter AC power. The observer never polls a
device or issues a command. All existing control, shutdown and reconnect gates
remain owned by the single controller. Entry options identify sources; channel
keys include entity IDs so changed sources cannot silently share a series.

Minute rows retain positive and negative Wh **estimates integrated from cached
power**, each channel's covered seconds, elapsed seconds and observed controller
states (including enabled, loaded package version and planning reason). These
are not certified energy-meter counters, measured savings or proof of hardware
response. Grid positive means import, negative export. BK DC output can include
direct PV: it is not a battery-discharge/cycle counter. PV input is not potential
generation, so it cannot establish curtailment. Actual battery cycling, costs or
curtailment remain unknown where independent evidence is missing.

Integration holds the previous power value only between valid endpoints at
most 15 seconds apart. State durations use the previous observation until the
next sample; transitions have that sampling resolution. Missing channel
endpoints yield uncovered time, not zero energy. Other valid channels still
count. Longer gaps and all restart/source-change boundaries contribute elapsed
`observer_gap` time without power estimates. Reserve, offline, disabled and
feedback states stay distinct; a `running` status is not itself proof of active
physical regulation. Absolute timestamps support later local day/night analysis.

The last 14 days of minute rows are stored atomically in the same HA Core Store
checkpoint as demand history and quality events, with readback. Snapshot copying
and restored evidence validation run in executor workers while the observer lock
prevents mutation; hardware-control callbacks keep running. Callbacks use the
wall clock when a delayed timer obtains the observer lock, so stale
scheduled timestamps cannot erase valid history or label current states future.
Genuine wall-clock reversal still fails closed. Legacy checkpoints
without operating rows retain demand history and begin new operating evidence
without inventing preceding coverage. Corrupt stores/readback errors fail closed
for planning and are not overwritten as an empty first install. A failed current
observation breaks subsequent coverage and exposes an error reason.

Durable checkpoints occur at most one minute apart and on normal unload. An
abrupt process loss can lose the unsaved tail; restore starts from the last
verified point and exposes the following gap. No minute is labeled fully
measured merely because it has a row. Diagnostics expose a bounded recent
summary; detailed retained data stay in the authorized HA installation, not in
GitHub fixtures or a new dashboard. Floating restoration tolerances cover only
arithmetic rounding, not hardware/export acceptance.

Internal diagnostics also aggregate all retained channel energy/coverage and
state durations, beyond the five-minute preview. This scan runs in an executor
under the observer lock. Totals describe the retained evidence, not an arbitrary
acceptance window or full-day extrapolation; partial coverage stays explicit.
The verified checkpoint timestamp identifies the last successful durable
readback. The diagnostic snapshot can include the newer uncheckpointed tail,
which is explicitly labeled. Missing channels remain absent, never zero-filled.

This capture does not automatically start or complete Sprint-2 acceptance. The
start must be recorded separately with the installed version/commit after
planning and capture have been checked. Earlier controller/learning records
are not new-feature acceptance. The internal comparison module remains modeled
baseline evidence; a counterfactual strategy was not simultaneously measured.
