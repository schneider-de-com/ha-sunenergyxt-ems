# SunEnergyXT EMS v0.1.1b9 — experimental Forecast.Solar source support

Select the new Forecast.Solar integration's **today** and **tomorrow** daily
energy sensors in the existing forecast options. They must be the correct roles
from the same config entry. The obsolete Solcast update-timestamp selection is
removed for that pair; operating settings and battery mappings are preserved.

The reader observes future successful owner-coordinator updates, then calls
HA's read-only `forecast_solar.get_forecast` cache service in raw resolution.
It never requests extra provider updates or owns API credentials. HA 2026.11.1
provides this service; older instances without it fail closed. Existing cache
data have unknown receipt age after attach/restart until an actual successful
owner update is observed. Failure, stale receipts, source reload/config changes
and unloading invalidate the reader. Diagnostics distinguish this local receipt
from unknown provider issuance.

Forecast interval Wh are preserved, including variable sunrise/sunset edges and
explicit night zeros. Half-hour subdivision assumes uniform production within
each supplied period; it is a forecast model, not measured household history.
Missing periods remain gaps and daily totals must reconcile with the selected
energy sensors. Watts are not integrated as if they were guaranteed samples.

Forecast.Solar supplies a point estimate without provider percentile bounds.
The nominal curve is available to the source adapter, but it **cannot authorize
a storage-priority hint** while uncertainty is unknown. No invented confidence,
PV allocation, grid charging, active tariff control or additional dashboard.
Missing AC/demand data continue to retain ordinary safe household regulation.
All hardware gates and the existing single writer remain unchanged.

For experimental installation: record current enable/planning intent, options
and the verified journal checkpoint; use the existing safe-off switch and
independently confirm actuator minima. Download this exact prerelease and
restart HA once. Verify the loaded version, retained options/journal, explicit
restart gaps and new source diagnostics before restoring the prior enable
intent after the existing safety checks. Bind the explicitly selected new
Forecast.Solar daily pair; do not substitute Solcast's old roof configuration.
Wait for normal owner polling instead of forcing another provider request.

Rollback: safe-off/readbacks, preserve options and journal, install exact b8
and restart. Its evidence format is unchanged, but b8 cannot consume this
Forecast.Solar pair: disable forecast planning during rollback rather than
silently restoring an obsolete Solcast source. Household control may be restored
after its existing checks. Re-enable planning intent only with a compatible,
verified source implementation. No full backup restore is required.

The seven-day Sprint-2 acceptance window has **not** started. Native lifecycle,
source-contract and regression tests do not constitute unattended hardware or
complete feature acceptance. Source details: [Forecast.Solar contract](docs/forecast-solar-contract.md).
