# Forecast.Solar energy stage: primary-source check

Research date: 2026-11-14. Scope: Sprint 2 #11/#92; HA Core
2026.11.1 and forecast-solar v5.0.1. Public documentation/source reads;
no forecast-data requests, configuration changes or hardware writes. The research
used public sources; a later parent-agent check read only the owner's already
available native diagnostics for the three non-secret adjustment fields.
Endpoint, interval and receipt semantics remain in
[forecast-solar-contract.md](forecast-solar-contract.md).

## Result

**The inspected sources do not establish whether the returned estimate is
unclipped module DC or energy at a particular AC delivery point, including which
conversion/system losses are already deducted. Keep the physical stage unknown.**
They do establish that inverter limiting and morning/evening damping can affect
the forecast. Module nameplate kWp as input does not establish DC as the output
stage; an inverter cap does not establish a complete AC conversion model.

## Explicit evidence

| Primary source | Established fact | Limit |
| --- | --- | --- |
| [Forecast.Solar calculation model](https://forecast.solar/#calculation-model) | Uses historic PVGIS irradiation by plane and weather forecasts; describes cloud and temperature effects on module performance. | Does not specify the electrical delivery point, inverter efficiency, a default system-loss percentage or exact server equations. |
| [Official HA configuration and estimation guidance](https://www.home-assistant.io/integrations/forecast_solar/#tweaking-the-estimations) | Damping reduces morning/evening estimates for shading; zero applies no damping. Inverter size limits forecasts when inverter capacity is below module capacity. | Describes adjustments, not a provider guarantee of AC energy after specified losses. |
| [Tagged HA coordinator](https://github.com/home-assistant/core/blob/2026.11.1/homeassistant/components/forecast_solar/coordinator.py#L98-L123), [constants](https://github.com/home-assistant/core/blob/2026.11.1/homeassistant/components/forecast_solar/const.py) | Passes module kWp, morning/evening damping and the inverter option to the client; positive inverter watts are converted to kW. Default damping is 0.0. | Public code alone cannot establish this installation's optional values; the native readback below supplies that narrower fact. |
| [Tagged client](https://github.com/home-assistant-libs/forecast_solar/blob/v5.0.1/src/forecast_solar/forecast_solar.py#L185-L214) | Sends these adjustments as API parameters; parses the response using `Estimate.from_dict`. | The client does not contain the server production/loss/clipping calculation. |
| [Tagged result model](https://github.com/home-assistant-libs/forecast_solar/blob/v5.0.1/src/forecast_solar/models.py#L71-L212) | Copies returned power and energy maps into the model. No electrical-stage, efficiency, loss-breakdown or uncertainty-bound fields are retained. | Absence of local loss calculation does not mean the server applies no losses. |
| [Provider's linked public Swagger definition](https://api.forecast.solar/swagger.yaml), reported version v7.41.0.2515 (2026-09-04), read2026-11-14 | Defines installed plane kWp and estimated power/energy result fields. | No electrical delivery point or loss/efficiency model is defined. Its period-energy definition says Wh while the referenced dataset description says kWh; do not use this ambiguity to override the independently tested HA/library Wh contract. |

The provider's [estimate documentation](https://doc.forecast.solar/doku.php?id=api:estimate)
and its linked damping documentation could not be inspected: the web tool
reported a non-retryable robots.txt restriction for `doc.forecast.solar`.
This is an evidence-access limit, not evidence that those pages contain no
answer. No access workaround was attempted. Current HA guidance was checked
with the web tool; version-specific behavior above comes from tagged code
retrieved through the GitHub connector.
The separately linked public Swagger YAML was read as static documentation via
a text download because the web reader rejects its YAML content type. No blocked
documentation page was retried through another route and no estimate endpoint
was called. That specification does not close the physical-stage evidence gap.

## Consequences and inference

The parent agent's native diagnostics readback on 2026-11-14 around01:22UTC
found the selected owner loaded, with no explicitly stored `inverter_size`,
`damping_morning` or `damping_evening` option. The tagged coordinator therefore
uses no optional inverter-size limit and damping0.0/0.0 for this configuration.
The resolved plane still reported34°/API−46°. This cache-only inspection changed
no setting and made no provider request. It excludes an explicitly configured
additional cap/damping as the explanation; it does not establish server-side
losses, electrical stage or the absence of all other production constraints.

Inference: clipping is a production constraint, while damping is an empirical
shading adjustment; neither supplies a documented battery charging/discharging
or DC-to-AC efficiency model. Inferring a PVGIS default loss factor merely from
the provider's use of PVGIS would also be unjustified.

The supplied installation has five identical 440 W modules on one plane:
2.2 kWp total, with 1.32 kWp connected to BK+B215 and 0.88 kWp to the other BK.
These are known nameplate capacities. A 3:2 division of the returned energy
would remain a modeling assumption, not provider evidence about actual
per-BK215 DC availability. Aggregate clipping cannot be reversed to recover
unclipped PV or automatically allocated between different storage paths.
Applying another generic loss factor could double-count losses; applying none
could overstate usable energy. These sources resolve neither choice.

Retain unknown stage and unknown uncertainty in internal evidence. This research
does not authorize active use of the point forecast, create confidence bounds,
or establish storage-specific production or usable household AC energy.

## Next check

First obtain an accessible copy of the provider's current estimate/damping model
documentation or a written clarification through its
[official support channel](https://forecast.solar/#support-channels). No message
has been sent. The precise question is: which electrical stage do `watts` and
`watt_hours_period` represent with no inverter limit, with a positive limit and
with zero; which inverter/system losses are included; and are limiting/damping
applied consistently before energy integration? Ask for model/version and
default coefficients, without sharing credentials or coordinates.

Then reconcile that answer with the native adjustment readback above before
defining any explicit stage-conversion model.
Existing DC telemetry may help evaluate a proposed model offline; agreement
alone would not prove provider semantics or household AC delivery. If an answer
does not establish the relevant stage, keep this gate unresolved rather than
activate a guessed conversion or per-storage split.
