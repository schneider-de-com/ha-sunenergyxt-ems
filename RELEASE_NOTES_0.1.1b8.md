# SunEnergyXT EMS v0.1.1b8 — experimental measurement-source diagnostics

The HA diagnostic download now identifies each configured inverter AC source,
its presence/availability, supported power unit, and separate HA value-change
and report timestamps and ages. A constant reported value is not automatically
a failed device. `device_sample_at` remains null: cached HA timestamps cannot
establish when the inverter actually sampled its physical output. Missing
sources keep null timestamps; arbitrary attributes and raw payloads are omitted.

This is a read-only diagnostic addition. No source eligibility, control,
reserve/DC/actuator/shutdown/reconnect gate or device protocol changes. No device
polling or second hardware writer. The native HA download is tested both loaded
and unloaded, including unchanged reports, unavailable/missing sources, privacy
and absence of service calls. Strict typing for diagnostics is a blocking CI step.

Before installation, record current options, enable/planning intent and the
verified evidence checkpoint. Use the existing safe-off path and confirm the
actuator minima independently. An unconfirmed offline shutdown remains a
blocker. Download this exact prerelease through HACS/Marketplace, restart once,
then independently read the loaded manifest, preserved options/obligations,
retained journal and new diagnostic fields. Restore prior enable intent only
after the existing safety checks; mark restart and measurement gaps.

Rollback: safe-off with readbacks, preserve the corrected options and journal,
download exact `v0.1.1b7` and restart. Both versions use the same evidence format;
b7 lacks only the new source diagnostics. No full backup restoration is needed.

Register investigations remain documented in
[the source ledger](docs/register-measurement-investigation.md). No faster AC
measurement or register interpretation is validated by this package. Additional
measurement hardware is postponed while documented alternatives are examined.
The seven-day Sprint 2 acceptance window does not start from this diagnostic
update. This remains experimental; CI is not unattended hardware approval.
