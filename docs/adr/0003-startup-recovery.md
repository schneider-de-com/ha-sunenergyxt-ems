# ADR-0003: Correct unwanted startup export without a permanent software latch

- Status: **Accepted behavioral requirement; implementation and physical validation open**
- Date: 2026-11-15
- Related: #53, #27, #68

## Context

At 16:35:22 CET on 2026-11-15, after an M80 limit change from zero to
0.1%, the signed Shelly net grid measurement changed to approximately
-1363 W. On 16:35:36 the EMS reported:
"Excess grid export during startup; switch EMS off to reset".
At 16:35:41 the meter reported about +351 W import again, but the EMS
remained in write_error and stopped dispatching useful output.

This is a confirmed software lockout and temporally associated startup
transient; the actual physical AC output attribution needs more evidence.
An isolated phase-localized change is not the signed aggregate net export.

## Decision

On confirmed **unwanted signed net export**, command a prompt supported
minimum to affected reachable hardware, record pending real-ACK
obligations and validate the outcome. Once the transient is over and
several **fresh, plausible, sequential** net readings show import, allow
controlled recovery without requiring the operator to toggle the EMS.
Do not immediately issue uncontrolled repeated 0%-to-positive start
commands; bounded retries, per-device output limits, ramp/settling and
the unresolved M80 physical startup behavior must be evaluated.

A positive-after-full PV export that the user explicitly selected is
not automatically classified as unwanted export. Maintain Q16: reported
BK215 DC output above 1920 W **alone** must not create a new automatic
EMS shutdown latch; preserve actual independent hardware/BMS protections.

## Consequences

This removes an avoidable long-lived outage but cannot guarantee physical
safe startup without separate evidence. The controller needs regression
replay of the 16:35 sequence, including device state, signed net, ACK
and possible repeated transients, followed by supervised hardware testing.

**No stable release may claim this decision implemented or validated
until the relevant tests and real AC behavior prove it.**
