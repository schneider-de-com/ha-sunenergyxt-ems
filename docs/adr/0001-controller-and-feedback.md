# ADR-0001: Single controller and independently verified inverter feedback

- Status: **Accepted as a design decision**; full v1.0 behavior not yet field-validated.
- Date: 2026-11-15
- Related: #27, #49, #68

## Context

An EMS target, successful HA number.set_value call and an optimistic
Solarman number entity value are not independent evidence that a Deye
actually adopted a power limit. Two controllers writing the same register
can also interfere with one another.

## Decision

Use one coordinated controller and **one serialized EMS writer** for all
configured inverters. Gate further increases on fresh actual Deye
register-53 readbacks after the requested command. Keep separately visible:
desired watts, dispatched watts, confirmed register value, measured
inverter AC when trustworthy, SoC and signed total grid value.

Treat missing, late, stale or contradictory evidence as unknown.
Do not generate a new competing-external-writer detection subsystem;
the operator must disable other controllers (approved Q17).

## Consequences

More complicated than blind writing, but correctness and recoverable
shutdown are observable. An HA service response is never sufficient
proof of actual safe hardware state. A Modbus register ACK still cannot
prove instantaneous physical AC output; physical behavior requires
time-aligned hardware investigation and live tests.

Supersede with an explicit new ADR if this architecture changes.
