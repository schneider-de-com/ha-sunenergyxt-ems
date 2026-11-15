# SunEnergyXT EMS architecture (arc42)

A compact [arc42](https://arc42.org/documentation/) reference, versioned
alongside the code. **Current** means observed in experimental b18;
**target** means user-approved for a not-yet-validated stable v1.0.0.
This document is not a hardware-certification claim.

## 01. Introduction and goals

Coordinate the existing BK215/B215 stacks and their Deye microinverter
power-limit entities so measured household **net** grid import is low, while
avoiding unintended aggregate export and preserving each battery reserve.

Quality priorities: physical-command truthfulness, recoverable faults,
safe explicit OFF, controlled actuator pacing, understandable diagnostics,
simple user settings and independently verified upgrades.

## 02. Constraints

- Home Assistant custom integration on HA OS/Proxmox; UI-first config.
- Two separate BK215 storage stacks currently drive M200 and M80.
- The integration issues HA number.set_value calls; it does **not**
  itself speak the battery vendor's transport or directly charge batteries.
- A single fresh signed aggregate Shelly grid-power sensor is the control
  observation: positive import, negative export. Individual phases are
  not required control inputs.
- Device names/entities and firmware behavior must be discovered and
  validated; never infer AC production from a percentage or BK215 DC alone.
- Existing installation/backups and current configuration must survive updates.

## 03. System context and scope

**Input systems:** Home Assistant Shelly Pro 3EM aggregate power,
SunEnergyXT BK SoC / lower and upper limits, independent inverter
health, Solarman number limits, physical Deye AC readings where fresh.
Optional forecast sources are experimental.

**Output systems:** one configured Home Assistant writable number entity
per Deye inverter plus read-only HA status/diagnostic entities.

**Boundary:** externally configured SolarMan/Modbus and BK integration
own the physical protocol. This EMS cannot assert an ACK from the optimistic
HA number state or from a modeled energy prediction.

## 04. Solution strategy

Maintain **one EMS controller and one serialized actuator writer**.
Plan desired aggregate power from fresh signed grid feedback, then
distribute it across independently eligible batteries with per-device
limits and reserves. Check actual register readback before treating writes
as accepted. Differentiate requested, acknowledged and physically measured
power; unknown is never zero. Read [ADR-0001](../adr/0001-controller-and-feedback.md).

Allow either user-selected no-export or measured-PV-after-full export,
without an implicit tariff or phase policy. Temporary export correction
must be recoverable; do not rely on a permanent software start-trip.
Read [ADR-0003](../adr/0003-startup-recovery.md).

## 05. Building-block view

| Component | Responsibility |
| --- | --- |
| config_flow | Guided setup, stored mappings and Options Flow (currently one long form). |
| runtime | HA subscription, critical-input freshness, output dispatch, pending ACKs and shutdown journal. |
| logic / pid | Pure controller, measured signed-grid regulation, battery eligibility and target allocation. |
| registry / discovery | Map HA devices, battery stacks and supported inverter entities. |
| sensor / switch / number | Operational state, read-only diagnostics and user controls. |
| observer / demand_history | Experimental measured-demand evidence; reject missing AC samples. |
| forecast / horizon / planning | Optional forecast priority hint; not stable v1.0 economic optimization. |

Keep pure decision logic independent from HA side effects.

## 06. Runtime view

**Normal:** sample signed net grid + device/SoC evidence -> validate
freshness -> calculate target -> dispatch only needed bounded limit updates
-> read real device register ACK -> resume closed-loop correction.

**Meter/device outage:** hold briefly if previously justified, then
reduce the affected output; unaffected batteries may continue.
Require fresh, plausible recovery evidence.

**Manual OFF/restart:** preserve desired ON/OFF state, issue safe minimum
once, retain unconfirmed hardware-stop obligations, and distinguish
an acknowledged stop from a merely queued HA service call.

**Startup export:** a severe transient should reduce output promptly.
A permanent manual-reset latch after a transient contradicts the agreed
recoverable behavior; see [ADR-0003](../adr/0003-startup-recovery.md)
and open #53.

## 07. Deployment view

HA OS VM on Proxmox -> HACS/custom integration -> Home Assistant services
-> Solarman/Modbus -> Deye M200 and M80 -> BK215 storage hardware.
Shelly feeds grid power independently.

No new server, cloud account, external database or second actuator
controller is required. Use existing Proxmox backups and the release
rollback procedure.

## 08. Cross-cutting concepts

- Separate command, confirmed register value and measured AC/DC.
- Reject stale/invalid telemetry, preserve recorder gaps and times.
- Keep explicit user OFF and per-battery SoC floors authoritative.
- Keep diagnostics human-readable with event-level logging, not a
  line per 5-second tick. No built-in complex notification engine.
- Keep everyday config separate from expert/device mappings in v1.0.
- Maintain small tests/PRs; replay historical negative cases.

## 09. Architecture decisions

- [ADR-0001 — Single writer and hardware feedback](../adr/0001-controller-and-feedback.md)
- [ADR-0002 — Stable v1.0 scope and evidence](../adr/0002-core-release-scope.md)
- [ADR-0003 — Recoverable startup export response](../adr/0003-startup-recovery.md)

ADRs record *context, decision, consequences* and can be superseded
only with an explicit successor ADR.

## 10. Quality requirements

Important observable acceptance:

- No unjustified continued net export; intentional PV-after-full export
  is assessed separately from unwanted export.
- A valid true register ACK is distinguishable from an optimistic HA write.
- Explicit OFF and lost-device cases never claim an unconfirmed shutdown.
- Stable controller converges without repeated limit hunting and without
  suppressing other eligible batteries when one goes offline.
- At least 24 hours of meaningful real two-inverter operation before
  stable 1.0.0; native HA tests and verified HACS update/rollback.

## 11. Risks and technical debt

The b18 M80 0%-to-positive transition can coincide with large signed
net export and a sticky software trip (incident 2026-11-15 16:35).
The instantaneous physical cause is **not** yet isolated. Investigate
#53 and #27 before unattended stable control.

Old b18 also contains a DC-sensor >1920 W software latch contrary to the
current user-approved policy. It must be removed without disabling real
manufacturer/BMS protections.

Economic forecast planning lacks seven days of sufficient real accepted
evidence and belongs to a separate later 1.x release. HACS/Marketplace
install/update/rollback acceptance is open.

## 12. Glossary

| Term | Meaning |
| --- | --- |
| Net grid power | Signed whole-home import (+) or export (-), not a single phase. |
| Commanded | EMS target, not proof a device received or implemented it. |
| ACK | Fresh actual inverter register-53 readback after a command. |
| AC / DC | Inverter physical grid-side output / storage or PV input power; do not mix. |
| SoC reserve | User-configured individual battery discharge lower bound. |
| Safe OFF | Lowest supported inverter limit with confirmed feedback, or explicit unresolved stop duty. |
| Stable release | Tested controller with observed hardware acceptance and rollback, not just a tag. |
