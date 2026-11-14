# Per-inverter availability diagnostics

Development main adds `inverter_availability` to the attributes of each
existing battery status sensor. Published b16 does not contain this addition.
No new dashboard, entity, device poll or hardware command is introduced.

The mapping is keyed by the configured inverter number entity. It explains
the first current HA availability check that fails, in this order:

| Value | Meaning |
| --- | --- |
| `actuator_unavailable` | The number entity is absent, unknown or unavailable. |
| `actuator_unit_invalid` | Its unit is neither W nor %. |
| `connection_unavailable` | An identified upstream connection entity is missing or is not on. |
| `ac_voltage_unavailable` | The supported Deye AC-voltage gate rejects the current source. |
| `available` | These HA availability checks pass. |

`availability_semantics` is
`current_ha_checks_not_physical_or_historical_cause`. Read the mapping together
with the battery role, SoC/reserve, DC output, pending acknowledgements and
the global EMS status. `available` is not a discharge authorization: reserve,
DC, shutdown/reconnect, manual disable and other control gates still apply.
Unsupported inverter profiles retain their existing AC-voltage behavior.

This is a current HA-cache observation, not an independently measured physical
connection, AC output or device acquisition timestamp. Multiple failed checks
can coexist; the mapping shows only the first one. Reading it after recovery
does not establish the earlier cause of an outage. HA Recorder can retain
published attribute changes under its configured retention/exclusion policy;
these attributes add no separate event store or guarantee of complete history.

The existing controller uses the same availability checks. The addition does
not introduce an export cutoff or modify reserve, DC, acknowledgement,
shutdown, reconnect or inverter limits.
