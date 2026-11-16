# ADR-0004 — Device-neutral tower and inverter capability boundaries

- **Status:** Accepted architecture direction; vendor adapter and hardware
  matrix implementation are **not yet accepted/field-validated**
- **Date:** 2026-11-16
- **Related:** #8, #27, #37, #49, #53, #68, #86
- **Architecture:** [arc42](../architecture/README.md),
  [compatibility matrix](../device-compatibility.md)

## Context

The first arc42 draft accidentally described one HA OS/Proxmox installation,
a Shelly Pro 3EM, two BK215 heads and two Deye M200/M80 inverters as the
system architecture. SunEnergyXT also sells BK215 Plus / B215 Plus and
manufacturer-compatible Deye, Hoymiles, APsystems and TSUN inverters.
Manufacturer electrical compatibility is **not** evidence that a given
Home Assistant integration has a write endpoint and real device ACK.

The existing EMS is partly generic: HA entity-based storage mapping,
watt-domain controller, signed grid power and `number` limits. Its safety
feedback is **not** generic: `runtime._hardware_feedback_matches` validates
one Deye/Solarman register-53 profile directly; for other integrations
the current fallback may rely on an optimistic HA number state.

## Decision

1. Model independent **storage towers**, not vendor-specific installations.
   A tower consists of one BK215 or BK215 Plus **head**, optional compatible
   B215/B215 Plus extensions, one SoC/reserve and configurable total Wh.
   Attached extensions are not independently dispatched. Never presume
   every tower has 2.15 or 4.30 kWh.
2. Keep the **pure control policy in watts** and signed whole-site grid
   power. The configured meter may come from any HA integration meeting
   freshness and sign requirements. HA OS, Container and other supported
   installation types are first-class deployment choices.
3. Resolve inverters via an **actuator capability contract**. A profile
   establishes write endpoint, actual rated power, min/max/step, scaling,
   settling, online and *independently verifiable hardware ACK* (plus optional
   measured AC); the controller is oblivious to brand, registry address
   and device-ID labels. Device discovery is only a *suggestion*.
4. Use one serialized writer per EMS regardless of brands; missing or
   unsupported ACK means **no claim of unattended compatibility**.
   Keep Deye-specific register-53 scaling confined to its verified
   profile; do not generalize it to Hoymiles, APsystems or TSUN.
5. Every new head/inverter/firmware/upstream-provider combination needs
   the applicable contract tests and real-hardware acceptance before
   being marked supported. The reference M200/M80 two-inverter 24h test
   is a **release acceptance scenario**, not a permanent product topology.
6. Record the model support and maturity explicitly in the
   compatibility matrix. Preserve existing mappings through upgrades.

## Alternatives considered

- **Make Deye M200/M80 and Shelly mandatory:** easiest short-term but
  excludes other genuine SunEnergyXT hardware.
- **Treat all manufacturer-compatible inverters as writable HA `number`
  devices:** attractive setup experience, but an optimistic state can
  falsely signal successful shutdown; rejected.
- **Implement every vendor protocol directly inside the EMS:** duplicates
  upstream integrations and couples releases to firmware; rejected.
- **Vendor-neutral HA boundary with validated per-device capabilities
  (chosen):** lets supported profiles grow without rewriting the control
  policy, while making limits and missing hardware evidence explicit.

## Consequences

The **architecture is portable now**, but the runtime needs focused work
to extract the Deye-specific ACK path and supply tested providers for
other manufacturers. Generic HA number selection must not be documented
as complete physical control. Plus capacity and tower limits cannot
be guessed from the head label; conservative existing defaults are
not certified Plus nameplate limits.

Discovery/configuration regression tests must include renamed BK215 Plus
heads, mixed towers, expansions and independent inverter mappings. Before
stable release, tests must distinguish UI success, HA write success, true
hardware ACK and measured AC/whole-grid response.

This ADR clarifies the product-wide scope of ADR-0001/0002 without
changing their historical one-writer/safe-release decisions. Replace it
only with an explicitly linked successor ADR.
