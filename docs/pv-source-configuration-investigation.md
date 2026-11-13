# PV source configuration investigation (Refs #11, #92)

Read-only source investigation, 2026-11-13; no HA/provider change, authenticated
provider request, forecast fetch or credential access was performed. Sources
below are public documentation and integration code tagged `v4.6.4`.

## Current evidence and azimuth convention

The supplied live diagnostic reports one existing site, capacities
`capacity=2.2` / `capacity_dc=2.2`, tilt34°, azimuth150°. User Memory dated
2026-10-31 describes five identical440Wp modules on the same unshaded roof,
170°/34°: three feed the BK with B215, two the other BK. Three further modules
are available for a planned expansion, not installed capacity. This note does
not independently remeasure the roof or read the user's provider account.

**`health_check.sites[].azimuth` is raw Solcast azimuth.** In
[`build_health_check_report`][health], `azimuth=site.get(...)` is returned
unchanged, while `compass_degrees` is computed separately. Site metadata comes
from [`GET /rooftop_sites` or its cache][sites]; a diagnostic can therefore still
contain previously fetched configuration.

The integration's [conversion function][conversion] explicitly uses
`compass_degrees = (-solcast_azimuth) % 360`: N=0, W=+90, E=-90, S=±180.
[Solcast's official OpenAPI][openapi], schema
`GetForecastRooftopPvPower.properties.azimuth`, independently says positive
angles are anticlockwise and east=-90. Therefore:

| Interpretation | Value |
| --- | --- |
| Current raw Solcast+150° | Compass210°, southwest of south |
| If remembered170° means ordinary clockwise compass bearing | Solcast−170°, 10° east of south |
| Changing raw150→raw+170 | Compass210→190°, **not** compass170° |

The same Memory record describes a western roof as 270°, establishing its
clockwise compass convention. The live HA diagnostic independently reports
`compass_degrees=210` / `compass_direction=SSW` for the current raw150° site.
The [integration setup guide][setup] also describes this sign convention and
recommends checking the site's facing-direction hint in the Toolkit.

## Supported configuration path

| Surface | Finding |
| --- | --- |
| HA reconfigure/options | Tagged [reconfigure schema][flow] edits API configuration. The [set_options schema][options] exposes forecast/dampening/limit settings, not azimuth, tilt or physical site capacities. No supported site-geometry writer was found. |
| Integration provider access | Site metadata is read by HTTP GET; forecast data is also fetched by [GET][fetcher]. Changing a cached HA attribute would not change the provider's physical model. |
| Existing hobbyist rooftop | [Official Solcast guidance][hobbyist] explicitly says: “Register and manage home PV systems in the Solcast Toolkit — not via the commercial PV power site API.” The documented management route is the existing Toolkit site. |
| Commercial resource API | The public OpenAPI has PATCH/PUT `/resources/pv_power_site`, a different resource family. Its capacities are in **MW**. This is not a documented update route for the existing hobbyist rooftop ID/account, and no entitlement, resource equivalence or write access was tested. |

Prepared manual correction procedure, pending angle verification:

1. In the [Solcast Toolkit][toolkit], select the **existing** Home PV system and
   match its site detail/resource ID to the current HA site. Do not add, delete
   or migrate a site; preserve the existing configuration for comparison.
2. In that site's configuration, correct only azimuth if its compass meaning is
   confirmed: compass170° means Solcast−170°. Check the facing-direction hint.
   Keep tilt34° and the existing capacities unchanged during this correction;
   five440Wp modules total2.2kWp DC. Do not include the three uninstalled modules.
   AC capacity is a separate inverter-model parameter, not automatically2.2kWp.
3. Read the saved site back in the Toolkit. Later, a controlled integration reload
   can refresh site metadata, followed by independent HA raw/compass readback and
   a forecast-source timestamp/version check. The [integration FAQ][reload]
   explains that site metadata is loaded on startup. Reload/forecast fetching
   may consume API quota or replace cached data; neither was performed here.

Do not use a forced forecast update, local dampening or a different API resource
as a substitute for correcting the physical source configuration.

## Per-site forecasts and a possible 3:2 model allocation

`solcast_solar.query_forecast_data` accepts an optional site ID. Its tagged
[action][query-action] calls the [cached forecast-list query][query], which
selects `site_data_forecasts[site]`; no provider fetch occurs in that query path.
This exposes forecasts per **configured rooftop**, not per BK or storage unit.
The one current rooftop therefore supplies one aggregate forecast. The tagged
[site metadata documentation][capacity] distinguishes AC `capacity` and DC
`capacity_dc`; commercial API units must not be copied into hobbyist fields.

Given the user-confirmed identical modules/orientation/tilt/shading and explicit
three-versus-two wiring, a proposed linear model could allocate60%/40% of the
same site forecast: installed DC nameplates1.32/0.88kWp. This is PV-capacity
weighting, **not** the batteries'2:1 storage-capacity ratio and not independently
measured per-store generation. Preserve source site/version, interval units,
freshness, source bounds and the explicit allocation assumption. Weather error
is shared between both allocations; they are not independent uncertainty bands.

Before adoption, resolve the azimuth discrepancy and check the forecast's energy
stage against the planner's expected BK PV input: modeled AC output is not
automatically unclipped DC input. Different branch losses, MPPT conditions,
storage curtailment and inverter clipping can invalidate exact linear scaling.
The M200/M80 have different nominal AC capacities; identical roof geometry alone
does not prove identical conversion behavior. No allocation, new rooftop,
provider-capacity change or planning acceptance is implemented by this note.

[health]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/custom_components/solcast_solar/actions.py#L948-L1008
[sites]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/custom_components/solcast_solar/sites_cache.py#L1099-L1140
[conversion]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/custom_components/solcast_solar/util.py#L17-L24
[openapi]: https://api.solcast.com.au/openapi/v1/openapi.json
[setup]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/README.md#L123-L150
[flow]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/custom_components/solcast_solar/config_flow.py#L266-L338
[options]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/custom_components/solcast_solar/actions.py#L178-L204
[fetcher]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/custom_components/solcast_solar/fetcher.py#L692-L704
[hobbyist]: https://docs.solcast.com.au/docs/section/rooftop-sites-hobbyist
[toolkit]: https://toolkit.solcast.com.au/
[reload]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/FAQ.md#L134-L140
[query-action]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/custom_components/solcast_solar/actions.py#L398-L427
[query]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/custom_components/solcast_solar/forecast.py#L80-L101
[capacity]: https://github.com/BJReplay/ha-solcast-solar/blob/v4.6.4/README.md#L874-L892
