# SunEnergyXT EMS v0.1.1b13 — retain original Forecast.Solar evidence

Observed successful Forecast.Solar updates now retain their parsed original Wh
intervals in a bounded private HA Store. Future internal assessment can refer
to what was forecast before measured production, across EMS restarts.
Diagnostics report historical count, receipt and persistence errors without
exporting raw curves or introducing a comparison dashboard.

Historical records never supply live freshness, provider issuance, confidence
or a planning priority. The point forecast's energy stage/allocation/uncertainty,
trustworthy household demand and measured benefit remain separate prerequisites.
No provider requests, device polling, additional writer or control-gate changes.

Storage uses atomic saves and independent readback. Corruption is preserved;
write/readback failures remain visible. Records are capped at 14 days/2048
snapshots; a crash before confirmation can lose a submitted receipt. This is
not a completeness or accuracy claim.

Update only through a checked experimental rollout: preserve current options,
enabled intent and journals; use existing safe-off and independent command-minima
readbacks, check restart readiness, then one actual restart. Read back loaded
version, history and options before restoring the original intent through normal
freshness/ACK/startup gates. Prepare exact published b12/721274d as rollback.
The new forecast Store is separate and remains untouched by b12's older demand
writer; no full restore or direct `.storage` edit is needed.

No unattended stable hardware approval or seven-day Sprint-2 acceptance follows
from CI or archival alone. See [contract and limits](docs/forecast-evidence.md).
