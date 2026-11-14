# Historical Forecast.Solar evidence (Refs #11, #92)

An original forecast must be retained before its predicted production can be
compared with later observations. The live consumer's expiring in-memory cache
alone cannot supply that evidence after a restart. The EMS now archives parsed
raw Wh intervals after genuine future owner updates, even while control is off.
This is an internal prerequisite for later forecast assessment, not calibrated
forecasting or permission for the planner to use a point prediction.

## Stored contract

The independent atomic HA Store `sunenergyxt_ems.forecast_evidence.<EMS entry>`
retains at most 2048 snapshots over 14 days. Each contains the owner entry ID,
observed successful coordinator-update receipt, owner's configuration modification
time when available, and explicit UTC interval starts/ends/Wh. It stores no
credentials, coordinates, arbitrary owner attributes, or new hardware readings.
Configuration modification is a provenance marker, not provider issuance or a
claim that geometry, shading, wiring or energy stage was verified.

The Wh curve passes structural/unit/finite-value validation, but daily-total
reconciliation remains the live consumer's separate check. Gaps and uncertainty
are retained; no omitted periods are filled with zero. Receipt is not weather
model issuance. Neither receipt nor archived intervals establish calibrated
confidence, available BK DC energy or a trustworthy per-storage allocation.
Raw curves stay in the private HA Store. Diagnostics expose only bookkeeping,
receipt and the explicit limits; no new entities or comparison dashboard.

## Lifecycle and failure

The existing owner-update callback submits a detached snapshot; serialized
background writes use atomic save plus independent readback. Exact duplicate
receipts are idempotent; conflicting content is not silently replaced. Failed
or unconfirmed writes remain marked and are retained in memory for the next
successful write. A crash before confirmation can lose a submitted receipt:
there is no claim of complete forecast coverage or zero-loss persistence.
During HA's stopping phase, Store may acknowledge saves and reads from RAM
before its final disk write; these are explicitly left unconfirmed as well.

Setup validates the historical Store; corrupt/unreadable data, including Core's
JSON-corruption Repair path, are not treated as first use and are not overwritten.
`persistence_ok` and `storage_error` are independent of the demand-capture flag.
`may_include_unconfirmed` marks in-memory records without verified persistence.
Missing initial cache receipts are not backfilled. No archival timer, provider
request, device polling, hardware writer or control gate is added.

Unload first detaches/cancels live source reads, then drains submitted evidence
writes. On reload the ledger is historical only: **live source receipt remains
unknown until a new genuine owner update**. Historical curves never restore a
planning priority, freshness or household training interval. Rollback to b12
leaves this separate Store untouched; its older demand writer cannot drop the
new records. A later compatible build can read them again. No full backup restore
or direct `.storage` modification is part of this rollback path.

## Evidence and remaining work

Behavior tests cover restart retention, duplicates/conflicts, bounded age/count,
malformed/future/nonfinite/overlapping input, save/readback failures and recovery.
Native HA tests exercise the actual EMS entry, owner coordinator, cache service,
registered status entity, atomic Store and unload/reload. They prove retained
historical snapshots with an unknown live receipt after reload and no extra
provider/cache call. Their cloud curve is a fixture, not a measured solar result.

Forecast-versus-measurement pairing, error analysis and defensible uncertainty
remain under #11; measured household history under #13 and active field benefit
under #12/#92 remain independent. Archiving alone starts no seven-day acceptance
window and provides no measured savings claim.
