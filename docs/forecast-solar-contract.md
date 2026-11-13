# Forecast.Solar cached-source contract (Refs #11, #92)

Source investigation, 2026-11-13: HA Core2026.11.1 and library forecast-solar5.0.1.
Primary-source legwork used public versioned code and documentation. A later
native HA inspection verified the configured plane and called the documented
raw cache service once; no provider refresh, mutation or credential access was
performed. That cache query did not establish receipt age or provider issuance.
The user selected the new Forecast.Solar integration. Supplied live metadata:
one installed south-facing plane, compass134°/tilt34°/2200W; three modules feed
BK+B215, two the other BK. West-facing modules are not installed. HA's
[plane conversion][coordinator] gives API azimuth `134 % 360 - 180 = -46°`;
this provider uses south0°, east−90°, unlike Solcast's convention.

## Read-only cache access

| HA surface | Returned data and limits |
| --- | --- |
| [Energy platform][energy] `async_get_solar_forecast(hass, entry_id)` | `{"wh_hours": {ISO period-start: Wh}}`, from cached `entry.runtime_data.data.wh_period`; no provider fetch. Despite the key name, periods need not be whole hours. No freshness metadata or explicit final endpoint. |
| [Service][service] `forecast_solar.get_forecast` | Parameters `config_entry`, optional `start`, `end`, `resolution` (`raw` default or `hourly`). Cached `{"watts": {ISO: W}, "wh_period": {ISO period-start: Wh}}`; start inclusive/end exclusive filtering, emitted in site timezone. No provider fetch. Use raw for interval work. |
| Entry's cached coordinator `Estimate` | Raw typed maps retain period-end keys. This is the owner integration's runtime data, not a stable provider-neutral API; pin/test access against HA lifecycle and version. Never instantiate another API client or call refresh for EMS reads. |

The energy-platform function returns `None` for absent/wrong-runtime entries;
neither it nor the service establishes `last_update_success` before returning
cached values. The Energy websocket [dispatch][dispatch] returns forecasts only
for entries selected in Energy preferences; absence there is not absence of the
new integration. No Energy preference change is needed to read its own cache.
The service's hourly mode averages power samples arithmetically and buckets Wh;
it must not define a new high-resolution power/energy model for this adapter.

## Units, endpoints and horizon

[Library `Estimate`][models] retains `watts: dict[datetime,int]`,
`wh_period: dict[datetime,int]`, `wh_days: dict[datetime,int]`, timezone and rate
limit. It parses timestamps using `datetime.fromisoformat`; do not silently
attach UTC to naive point timestamps. Daily date keys are date labels, not
physical acquisition timestamps. [Library requests][client] ask for `time=utc`;
HA's service converts output keys to the reported site timezone.

The [provider contract][provider] describes Watts as average power for the
period, and `watt_hours_period` as Wh from the **previous timestamp to the key's
timestamp**. HA's service docstring instead calls Watts instantaneous. This
semantic conflict makes Wh intervals the safer basis; do not infer energy by
holding Watts constant or treating irregular samples as uniform hourly bins.

HA's [mapping helper][coordinator] sorts raw end timestamps and assigns
`raw_wh[end]` to the previous timestamp, the interval start. It skips a zero at
midnight because that starts an API day rather than ending an energy interval.
Keep explicit endpoints/durations: sunrise/sunset edges can include seconds,
and intervals can vary. Converting Wh to interval-average W requires actual
elapsed hours. The first raw point has no preceding interval; the final start
map alone does not encode its closing endpoint. Trimming before reconstructing
intervals can discard necessary boundary data.

Tagged library [fixtures][fixtures] demonstrate zero-power sunrise/sunset points,
day-start zero-energy markers, variable edge periods, and public hourly versus
personal half-hourly series.
They are contract examples, not evidence about the user's actual generation.
An explicit zero may describe a validated forecast interval; it never justifies
padding omitted periods, a missing day, unknown night coverage or times outside
the returned horizon with zeros. Preserve skipped day markers and uncovered
intervals as gaps unless a separately justified night model is explicitly used.
Reconcile period sums with `wh_days` before treating a day as complete.

## Freshness and uncertainty

Raw provider examples contain `message.info.time`/`time_utc`, but
`Estimate.from_dict` [drops those fields][models]. They are response metadata,
not independently established weather-model issuance times. `Estimate`, the
diagnostic payload, Energy platform and get_forecast service expose no retained
provider-issued or received timestamp and no P10/P90/confidence interval.
`wh_days`, queried sensor timestamps and the time the EMS reads a cache are not
substitutes for issuance or successful retrieval time.

The normal [coordinator type used here][update-coordinator] exposes `last_update_success`,
`last_exception` and `async_add_listener`, but no last-success datetime. The
Forecast.Solar coordinator polls public accounts hourly, authenticated accounts
every30min, and inherits `always_update=True`. A consumer can record a **local
receipt-of-success notification** for future observed successful updates while
retaining its meaning separately from provider issuance. Do not refresh age on
failure or simply when attaching to an existing cache. Initial/restored cache
age remains unknown without a valid receipt; reload/source changes must
invalidate provenance and detach/reattach listeners. Existing owner polling
continues to own requests and quota; an EMS read must not trigger extra polling.

These are point forecasts. Do not invent percentile bounds or describe
`lower=expected=upper` as calibrated confidence. Unknown uncertainty remains
explicit; any additional conservative engineering envelope needs a named,
validated assumption rather than a provider-confidence claim. The API documents
installed **module kWp** as input, while the library can pass an optional
inverter-kW limit. This does not by itself establish conversion losses, unclipped
BK DC availability or actual per-storage generation. Those stage/model limits
must accompany any explicit3:2 allocation of the same south-plane forecast.

## Implemented consumer and remaining gates

The EMS recognizes the integration-owned today/tomorrow registry roles from one
entry, even after entity renaming. Selecting that pair removes the inapplicable
Solcast timestamp binding. Existing operating options remain unchanged. HA
2026.11.1's cache service is required; an older instance without it fails closed.

The reader subscribes to successful owner updates and records their local UTC
receipt. Replayed callbacks with the same data object do not refresh age. It
requests raw cached intervals once per observed update, with a bounded timeout,
generation/configuration checks and cancellation at unload. Provider failure,
replacement/reload and configuration changes invalidate the snapshot. Initial
cache age remains unknown. A receipt older than two hours (two expected public
refresh periods) is ineligible; this is consumer policy, not provider issuance.

Endpoint timestamps reconstruct start-keyed Wh periods; missing energy or final
boundaries are rejected. UTC half-hour resampling conserves the period budgets
and explicitly models uniform within-period production. Long night zeros must
exist in the source. Selected Wh/kWh daily sensors must reconcile; neither gaps
nor unreturned time are zero-filled. Diagnostics omit raw curves and arbitrary
owner data, retaining entry, receipt, unit and unknown-uncertainty semantics.

The resulting nominal forecast has no lower/upper bounds and no provider-issued
timestamp. It is deliberately prevented from authorizing a planning priority
while uncertainty is unknown. AC/demand coverage and per-storage modeling also
remain independent prerequisites. The new source does not start acceptance or
claim a completed Sprint-2 planning feature.

[energy]: https://github.com/home-assistant/core/blob/2026.11.1/homeassistant/components/forecast_solar/energy.py#L8-L24
[service]: https://github.com/home-assistant/core/blob/2026.11.1/homeassistant/components/forecast_solar/services.py#L27-L144
[coordinator]: https://github.com/home-assistant/core/blob/2026.11.1/homeassistant/components/forecast_solar/coordinator.py#L35-L187
[dispatch]: https://github.com/home-assistant/core/blob/2026.11.1/homeassistant/components/energy/websocket_api.py#L184-L234
[models]: https://github.com/home-assistant-libs/forecast_solar/blob/v5.0.1/src/forecast_solar/models.py#L71-L212
[client]: https://github.com/home-assistant-libs/forecast_solar/blob/v5.0.1/src/forecast_solar/forecast_solar.py#L185-L214
[provider]: https://doc.forecast.solar/doku.php?id=api:estimate#content
[fixtures]: https://github.com/home-assistant-libs/forecast_solar/tree/v5.0.1/tests/fixtures
[update-coordinator]: https://github.com/home-assistant/core/blob/2026.11.1/homeassistant/helpers/update_coordinator.py#L80-L190
