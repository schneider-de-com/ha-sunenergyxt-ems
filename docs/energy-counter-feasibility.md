# AC energy-counter feasibility (Refs #13, #53, #92)

Internal investigation, 2026-11-13, on main `10909a843b191b22c302179d796f4b6b0307b55b`.
No code, HA configuration or hardware behavior changes. Existing counters may
support a coarse longer-period cross-check; they are not yet a validated
half-hour learner input or instantaneous feedback/safety measurement.

## Source contract

The supplied profile and tagged [upstream micro profile][profile] define daily
production at r60 and cumulative production at r63/64, both in 0.1 kWh: 100 Wh
per count. The [register investigation](register-measurement-investigation.md)
records model/firmware limits; DC power and actuator commands are not AC energy.

Solarman 25.08.16 [selects][factory] `SolarmanPersistentSensor` for `persistent`.
Its [implementation][persistence] always returns `available=True` and inherits
restoration of the previous native value. `ensure_increasing` is a separate,
optional flag: only when present does it reject a lower **positive** new value;
zero is not rejected by that condition. The supplied cumulative profile has
`persistent`, without `ensure_increasing`. Thus numeric/available and
`total_increasing` metadata do not establish freshness, monotonicity, absence of
resets or continuous coverage. Persistent does not imply reset suppression.

## Read-only observations

The read-only investigation returned complete HA history for
**2026-11-12T18:00:00Z–2026-11-13T18:35:00Z**. The native response and actual request parameters were checked against the
figures below; no new HA query was needed. Berlin times are UTC+01:00.

| Source | Returned observations | Limit |
| --- | --- | --- |
| M200 cumulative | 24 rows; synthetic start unavailable; first numeric 13 Nov 07:56:04.364978 Berlin =2766.7 kWh; last 19:30:40.042931 =2768.9 kWh; 22 increments of 100 Wh | Reported difference 2.2 kWh over 11h34m35.677953s of report time, **not exact full-window/day production** |
| M200 daily | 23 rows; first numeric 07:56:04.364686 Berlin =0; last 19:25:44.360754 =2.1 kWh; 100 Wh steps | Different endpoints/quantization; do not add daily and cumulative counters or call their difference measured loss |
| M80 cumulative | Only synthetic window-start snapshot =891 kWh; no changed value | No observed change does not prove zero physical production or valid coverage |
| M80 daily | Synthetic start =0.4 kWh; reported reset to0 at 13 Nov 00:11:37.424873 Berlin; no later increase | Reset report is not exact physical reset time; held zero is not verified zero output |

M200 cumulative's first increment followed its first numeric report by 246.1659 min,
then 14.9167–24.97375 min; daily's first spacing was 261.066 min, then 14.9333–29.8784 min.
These are threshold-crossing/report spacings, **not acquisition cadence**. A
frequently polled quantized counter can remain unchanged; cache/gaps can also
contribute. Current reported totals were 2768.9/891 kWh, daily 2.1/0 kWh, all
`kWh`/`total_increasing`; those metadata do not repair missing acquisition evidence.

Shelly candidates `sensor.heizungsraum_shelly_pro_3em_total_active_energy` and
`..._total_active_returned_energy` were inventoried as kWh/total_increasing only.
Their history bounds, resets, coverage, signs and alignment remain unvalidated.

## Conditional energy balance and uncertainty

For matching physical intervals and complete topology:
`household energy = (grid import energy - grid export energy) + sum(all inverter AC energy)`.
Every generation/storage path must be included; unknown paths are not zero.
Matching phase-summed import/export counters can yield signed net energy after
semantic validation. Their separate gross totals need not equal billing totals
from a meter that nets phases before accumulating import/export; no tariff-cost
claim follows from unvalidated gross values.

Assume a stable uniform monotone 100 Wh quantizer (fixed truncation/rounding), valid
aligned endpoints, no resets and no missing period. Then delta quantization
error is at most 100 Wh per counter, hence at most 200 Wh for two inverters:
at most 400 W averaged over 30 min, or at most 100 W over 2 h. These are **conditional
arithmetic bounds, not measured accuracy/confidence**. Grid quantization,
acquisition lag, alignment, resets, missing periods and meter error are extra.
No hard acquisition-lag bound or physical maximum follows from the nominal
inverter ratings or reported update spacing. Longer intervals do not repair gaps;
never split a 2 h total into four invented half-hour learner observations.

## Reproduction and next validation

The actual read-only history request used the four entities together:

```json
{
  "entity_ids": [
    "sensor.heizungsraum_deye_m200_total_production",
    "sensor.heizungsraum_deye_m80_total_production",
    "sensor.heizungsraum_deye_m200_today_production",
    "sensor.heizungsraum_deye_m80_today_production"
  ],
  "start_time": "2026-11-12T18:00:00Z",
  "end_time": "2026-11-13T18:35:00Z",
  "limit": 500,
  "minimal_response": true,
  "significant_changes_only": true
}
```

The response reported effective `order="desc"`, `offset=0`, and no further
pagination. Preserve each
source's availability/reset records and inspect `has_more` when reproducing.
A window-start snapshot can be carried forward from before the window; do not
use it as a fresh endpoint, change event or cadence proof. Recorder change
history alone does not expose every unchanged successful device read.

Next, reconcile compatible **longer** intervals with grid endpoints and source
availability evidence. Validate units/topology/word order/quantizer, daily versus
cumulative consistency, reset/rollover/restore behavior, and actual endpoint age
or defensible bounded uncertainty. Validate grid phase-netting separately from
billing semantics. Reject incomplete/unbounded intervals; unsupported, missing or
held-zero values remain unknown. Do not clamp inconsistent negative reconstructed
load into apparent validity. Preserve any useful estimate's original interval
and uncertainty. An implementation would need RED/GREEN gap/reset/rollover/source-
change/restart cases and review. Present evidence does not justify replacing the
[half-hour learner](demand-learning.md), relaxing gates, starting Sprint-2
acceptance, or making additional hardware a mandatory decision.

[profile]: https://github.com/davidrapan/ha-solarman/blob/v25.08.16/custom_components/solarman/inverter_definitions/deye_micro.yaml#L436-L517
[factory]: https://github.com/davidrapan/ha-solarman/blob/v25.08.16/custom_components/solarman/sensor.py#L20-L39
[persistence]: https://github.com/davidrapan/ha-solarman/blob/v25.08.16/custom_components/solarman/sensor.py#L98-L112
