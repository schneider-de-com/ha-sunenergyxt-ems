# Forecast source contract (Sprint 2)

The Solcast adapter reads supplied Home Assistant states; it does not refresh a
provider, register a task, change configuration or write to a device. Runtime
source selection and planner integration are separate work under #11/#92. The
existing household regulator is unchanged by this adapter alone.

The caller must map day sensors and the last-success timestamp from the same
verified Solcast config entry. Today and tomorrow are supplied in interval order.
Missing, unavailable, stale, malformed, overlapping or incomplete data raise
`ForecastError`; callers must retain safe household regulation rather than invent
zero production. The requested horizon is explicit and must include the desired
night. Absolute UTC intervals handle local midnight and daylight saving changes.

Provider contract verified against
[BJReplay/ha-solcast-solar v4.6.4](https://github.com/BJReplay/ha-solcast-solar/tree/v4.6.4/custom_components/solcast_solar):
`const.py` defines `detailedForecast`/`period_start`/`pv_estimate`; the
`solcastapi.py` method `forecast_entry_update` integrates estimates using 0.5 h
for daily kWh. Consequently nested estimates are half-hour mean kW, although
the parent sensor reports daily kWh. `detailedHourly` is not substituted.

The adapter requires `dataCorrect: true`, the parent's kWh contract, a successful
API timestamp and parent state updates within 24 h (5 min future tolerance).
A recent parent report cannot refresh an old API timestamp, and a recent API
timestamp cannot certify a parent older than 24 h. Parent `last_updated` is only
an additional freshness check, not evidence of provider generation time; unchanged
forecasts can conservatively become ineligible. The result preserves the API
timestamp, oldest parent update and source entity IDs for diagnostics.

Expected, lower (`pv_estimate10`) and upper (`pv_estimate90`) estimates are
validated separately and must be ordered. These are provider uncertainty
estimates, not guarantees, measured energy or a claim of calibrated confidence.
The planner still needs a policy for uncertainty and tower-specific allocation.
This adapter does not infer that an aggregate PV forecast belongs to one battery.

The committed tests use synthetic forecasts and native HA states. They cover
midnight, daylight saving, explicit source night zeroes, gaps, uncertainty and
freshness. A separate local read-only replay matched a live source's daily
aggregate; the detailed private household forecast is not exported to GitHub.
That check is not a live planning or hardware acceptance run.
