# Household demand forecast (Sprint 2)

`demand_forecast` learns a local-clock half-hour profile from measured household
energy. It is pure domain logic; HA history acquisition, persisted measurements,
source verification and runtime fallback are subsequent integration work in
#13/#92. This module alone changes no controller behavior.

Input intervals must be complete, reliable, chronological, non-overlapping
30-minute UTC slots with nonnegative household energy. Missing intervals remain
absent. A grid-only reading while solar/storage supplies the house is not total
household demand. The HA boundary must establish either an independent household
measurement or synchronized signed grid plus all independent AC contributions;
missing inverter measurements must not become zero. Device commands, BK DC output
and forecast values are not measurements of AC household consumption.

The learner uses up to 28 days, grouping by local clock half-hour. More recent
behavior has exponentially greater weight, with a seven-day half-life. Each
requested clock bucket needs measurements on at least three distinct local dates
and one measurement no older than three days. These are conservative engineering
eligibility thresholds, not a claim of calibrated prediction accuracy. Insufficient
or stale support raises `ForecastError`; it does not create a default household
profile. Seasonal/cycle objectives remain the planner's responsibility.

Repeated autumn clock hours are averaged within their local date and count as
one support day. Output intervals remain contiguous absolute UTC time and may
clip the current and final half-hour, up to a 48-hour horizon. Each interval
retains its number of supporting days. The result reports the newest actual
history endpoint, not the time a prediction happened to be requested.

Lower/upper values are the observed minimum/maximum of the matching bucket in
the retained history. They are empirical variability bounds, not probabilities
or guarantees about future events. The planner must still assess uncertainty
and may retain ordinary household regulation even with a valid forecast. No
weekday/weekend distinction or learned appliance schedule is claimed.

Synthetic tests cover clock profiles, recency weighting, sparse/stale history,
invalid energy, duplicate/future/partial samples, midnight and both DST changes.
They are not evidence of real installed learning or seven-day feature acceptance.

## Collecting complete intervals

`DemandHistory` integrates previously validated household power observations,
holding a value only until the next observation, at most 90 seconds later.
This is sampled-power integration, not an exact billing meter or a claim about
unobserved load changes. Missing values, long silence, invalid measurements,
out-of-order timestamps and conflicting duplicates break coverage. Only complete
UTC half-hours enter learning; starting mid-slot leaves the prefix unknown.
Explicit measured zero power is allowed; a missing reading is not zero.

The versioned JSON representation contains complete intervals only. Restoring it
validates chronology, duration, values and future times, retaining up to 28 days.
Partial slots and held power do not survive a restart, so downtime cannot be
bridged. The caller must actually persist the representation, handle a failed
save, and collect verified HA sources before this becomes installed learning.
This class does not access the filesystem or schedule collection itself.

The seven-day acceptance evidence additionally needs an operating/gap ledger;
the absence of intervals alone does not explain when or why observation failed.
That ledger and actual HA storage lifecycle remain separate integration work.

## Passive Home Assistant collection

An entry now owns a separate `DemandObserver`: it reads cached HA states every
five seconds, even while ordinary control waits for feedback or is switched off.
It does not poll devices or call services. Unload cancels its callback and flushes
storage; the existing controller retains all hardware writes and safety gates.

The observation is signed grid power plus every configured inverter's independent
AC source. Missing/offline/stale sources, duplicate AC mappings, known DC/PV sources
mapped as AC, implausible totals and source times more than 30 seconds apart are
ineligible. AC uses the existing conservative state-change freshness contract;
unchanged reports and offline inverters can therefore leave learning gaps. HA
timestamps are not guarantees of synchronized physical sampling. Every additional
generation path must be represented before this reconstruction is suitable.

The HA `Store` checkpoints completed history and a separate quality-transition
ledger each minute and at unload, with immediate readback. A crash can lose up to
the last checkpoint period and the current partial half-hour; restart marks the
unconfirmed period as `observer_gap` rather than bridging it. Corrupt/unreadable
stored evidence is preserved and collection remains ineligible until it can be
read. Failed saves/readback set `persistence_ok: false`, independently of hardware
control. This flag must gate subsequent planning/evidence claims. Source changes
discard incompatible training and retain a marked source change in the ledger.

The ledger retains two weeks of quality transitions, one preceding anchor and
the last poll timestamp. It records observation quality, not active planning or
proof of continuous hardware operation. Diagnostics expose current quality,
checkpoint errors and recent transitions; they omit raw household traces.
Forecast binding, plan decisions and installed feature acceptance remain later
work. No acceptance window starts merely because passive collection exists.

Core's JSON-corruption recovery (rename, Repair, `None`) is detected through its
persistent storage-corruption Repair. It is not treated as first use: observation
stays ineligible while the checkpoint cannot be recovered. Future-tolerated HA
source times also cannot create measured energy beyond the observer's wall time.
