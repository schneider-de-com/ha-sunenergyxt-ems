# SunEnergyXT EMS v0.1.1b7 — experimental PV-options repair beta

Packages #106: optional PV input power now rejects kWh energy counters,
missing entities and unsupported units. The chooser filters power sensors;
W/kW sources may remain configured while unavailable, with runtime freshness
checks unchanged. **Battery N: remove optional PV power source** explicitly
clears an existing mapping, including an invalid one, without choosing a
replacement. English/German labels and errors are included. Also includes
#91's reported-limit diagnostic availability correction, merged after b6.

No controller, hardware writer, reserve/DC/reconnect gate or dynamic-tariff
behavior changes. No inferred AC zeros or missing forecast data are introduced.

Before installation, capture current options and evidence checkpoint. Use the
existing safe-off path and independently confirm actuator shutdown; an offline
inverter with an unconfirmed shutdown remains a blocker. Install this exact
prerelease through HACS/Marketplace, restart and read back the loaded version,
entry, preserved options, shutdown obligations and retained journal. The
existing single writer remains the only hardware controller.

Check the actual Integrations UI and the removal option. After removing an
invalid mapping, read it back independently and verify the remaining settings.
An options tool reporting success is insufficient. Keep the previous enabled
and planning intent recorded; any restoration must pass the existing gates.
Invalid/stale AC or demand data continues household fallback and does not
establish successful forecast planning. A seven-day acceptance start requires
separate checked planning and durable capture; it never starts from this update.

Rollback: safe-off and confirmed readbacks, preserve options and the verified
journal, re-download `v0.1.1b6` and restart. B6 shares the capture format but lacks
this options repair. Retain a corrected mapping rather than restoring a known
invalid source. Mark any restart/measurement gap. For older b4, follow the
protected-checkpoint instructions in [b5 notes](RELEASE_NOTES_0.1.1b5.md).

This remains experimental; CI is not stable unattended hardware approval.
