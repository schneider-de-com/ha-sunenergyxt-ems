# SunEnergyXT EMS v0.1.1b11 — experimental planning input visibility

The existing planning-status entity now shows independent household collection
quality, complete interval count, verified persistence state and selected
Forecast.Solar cache receipts. A missing demand interval no longer hides whether
the new forecast source has received a normal update. No extra dashboard,
provider request, device poll, writer or control-gate change is introduced.

Unknown collector results stay null before setup. Cached point forecasts retain
unknown issuance, uncertainty and PV energy stage; they do not authorize a
planning priority. The primary fallback reason remains authoritative.

Checked experimental update: preserve options, original enable intent and the
latest verified journal checkpoint; use the existing safe-off and independent
actuator-minimum readbacks. Install exact b11, restart once, independently read
the loaded version, attributes, unchanged options and retained journal/gaps.
Restore original intent only after existing safety checks. No configuration
change is needed for these diagnostic attributes.

Rollback: same safe-off/readbacks, exact published b10 and one restart; preserve
options and journal. Both use the same capture format. Mark the restart gap;
do not perform a full backup restore or reconstruct missing observations.

This remains experimental and does not start the seven-day planning acceptance
window or certify real hardware response, energy savings or demand coverage.
