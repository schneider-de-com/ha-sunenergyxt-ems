# SunEnergyXT EMS — software architecture (arc42)

> **Architecture of a reusable Home Assistant integration, not an inventory of one household.**
> Current release line: experimental `1.0.0-beta.2`; next validation candidate:
> `1.0.0-beta.3`; stable `1.0.0` is a separate hardware acceptance gate. A *designed extension point* is not an implemented or
> hardware-verified feature. Site-specific incidents and examples belong in
> issues, tests and [the handover](../HANDOVER-2026-11-16.md), **not** in the
> product architecture.

The twelve sections follow [arc42](https://arc42.org/). See
[hardware and software compatibility](../device-compatibility.md) and
[ADR-0004](../adr/0004-device-neutral-battery-and-inverter-boundaries.md).

## 01 — Introduction and goals

The EMS coordinates **independent storage towers** in the SunEnergyXT
**BK215/BK215 Plus** family and one or more compatible, separately controllable
microinverters per tower. It follows **signed, aggregate grid power**, honours
each tower's discharge reserve and makes safety and actual device feedback
observable. An extension battery (**B215 or B215 Plus**) increases the capacity
of its head unit; it is **not a standalone dispatchable tower**.

Supported system configurations must be data-driven: the topology, number of
towers, expansion capacity, inverter brands, rated output and meter are **not**
coded for a particular house.

Quality goals, in priority order:

1. Never claim a physical write, zero output or safe shutdown from an
   optimistic Home Assistant state.
2. Predictable OFF, per-tower reserve, independent error isolation and
   recoverable ordinary control deviations.
3. Stable bounded regulation with observable input freshness and command
   timing, no competing actuator writers.
4. UI-first mapping, diagnosability, testability and reversible upgrades.

## 02 — Constraints

- This is a **Home Assistant custom integration**, not a standalone EMS
  daemon. Home Assistant OS, Container and other supported HA installations
  are deployment options; Proxmox, Shelly and Solarman are **not prerequisites**.
- The EMS consumes existing HA entities and services. The upstream battery
  and inverter integrations own authentication, local protocols, firmware
  support and device-specific commands.
- Use **one signed whole-site meter** in W/kW: positive = net import,
  negative = net export. Its brand and entity ID are configurable. No
  mandatory per-phase sensors or phase-specific control.
- Storage is modelled as **one head unit plus zero or more extensions**,
  constrained by the actual manufacturer's topology. Preserve each tower's
  independent reserve and configured total capacity.
- Only one EMS owns the selected limit entities. Other controllers must
  not write those actuators at the same time.
- No generic Modbus address, percentage scaling, minimum setpoint, AC
  rating, response time or ACK semantics may be assumed for every
  manufacturer/model/firmware.
- Preserve equipment mappings, output limits, reserve settings and EMS enable intent
  during beta upgrades. Obsolete experimental algorithm flags may be ignored;
  hardware acceptance and installed-version compatibility must be explicit.

## 03 — Context and scope

```text
[SunEnergyXT BK head(s) + optional B215/B215 Plus extensions]
          | local/provided battery integration; SoC, reserve, DC/PV
          v
[Home Assistant entity/state registry] <--- [whole-site signed meter]
          ^                                  (Shelly/other)
          |
[Vendor inverter integration(s)] <----> [microinverter(s)]
  Deye/Solarman, Hoymiles, APsystems, TSUN   (physical AC)
          ^
          | writable power-limit + trusted feedback contract
          v
[SunEnergyXT EMS]
  config -> validate -> allocate -> dispatch -> confirm -> observe
          |
          v
[HA status, user controls, diagnostics, history]
```

**Inside:** controller policy, tower eligibility, actuator orchestration,
state/error publication, configuration, optional forecasts. **Outside:**
battery firmware/BMS, inverter protocol implementations, meter hardware,
automatic hardware discovery guarantees and physical installation.

A manufacturer declaring an inverter **electrically compatible with a BK
battery** says nothing by itself about a writable HA power limit or a
trustworthy EMS acknowledgement. Both contracts must be checked separately.

## 04 — Solution strategy

Use a **vendor-neutral core** with two distinct boundary contracts:

1. **Storage-tower input:** stable HA device identity, fresh SoC, per-tower
   lower/upper SoC and optional PV/DC/charging observations; total capacity
   is configured from actual head plus extensions, not guessed from a model.
2. **Inverter actuator:** HA writable limit, known unit/range/step/rated AC
   watts, verified online state and model-specific method to confirm
   accepted limits. Actual AC production is a separate observation.

The pure controller allocates *watts* among eligible towers/inverters; a
device adapter converts to supported vendor units, enforces granularity,
settling/cooldown and confirms a real readback. The controller must never
know `number.my_m200`, a firmware register address or a Shelly entity ID.

The **current runtime** already has independent tower records, generic
`number` limits and Solarman-specific register-53 verification.
**It does not yet implement general-purpose vendor adapters** or validated
ACKs for Hoymiles/APsystems/TSUN. See [ADR-0004](../adr/0004-device-neutral-battery-and-inverter-boundaries.md)
and the compatibility matrix. This distinction is a release gate, not a
documentation footnote.

## 05 — Building-block view

| Building block | Responsibility | Device-specific? |
| --- | --- | --- |
| `discovery.py` / `registry.py` | Stable source-owned role discovery; tolerate user renames; expose candidates without claiming support | Registry roles vary by upstream provider |
| `config_flow.py` | UI-first tower-to-inverter mapping, manual correction, ratings, mode and source selections | Device selection via HA entities, not model strings |
| `logic.py` | Single bounded adaptive proportional signed-grid feedback, eligibility, reserve and fair allocation in watts | **No** |
| `runtime.py` | Serialized control ticks, freshness, emergency reductions, dispatch, pending ACK and persisted shutdown duties | **Currently mixes generic runtime with Deye-specific verification; extraction pending** |
| **Actuator capability boundary (target)** | Normalize limits, min/max/step, connectivity, readback, startup semantics and truthful status | **Yes; distinct profile per validated model/firmware/provider** |
| `sensor.py` / `switch.py` / `number.py` | User controls and independently labelled requested / acknowledged / measured diagnostics | **No**, except entity source selection |
| `observer.py`, `operating_history.py` | Passive observations with missing/stale values preserved | **No** |
| `forecast.py` / `horizon.py` / `planning.py` | Experimental external forecast hint, optional and isolated | Provider-specific source contract |

**Invariant:** a `number.set_value` service returning successfully, an HA
`number` state changing, a true register ACK and measured AC watts are four
different facts. They must have distinct status fields.

## 06 — Runtime view

**Setup:** discover BK215/BK215 Plus head-unit entities through the official
SunEnergyXT-BK integration (legacy and current domains); associate each head
with its **own** inverter limit entities. Extensions contribute to the head's
capacity but must never appear as independently dispatched batteries.
Validate a unique signed site meter, source units and single-write ownership.

**Normal operation:** fresh net-power sample -> independently eligible towers
-> per-inverter watt targets -> supported unit/rate conversion -> serialized
write -> trustworthy device acknowledgement -> settling and a later fresh
meter sample -> next correction. Early second/third inverter participation
is policy, not hardcoded M200/M80 ordering.

**Loss of one tower:** isolate the affected tower and keep an independently
safe tower eligible. **Loss of common meter:** stop positive increases and
apply bounded safe fallback. **Lost ACK:** do not advance an unverified
actuator; retain unresolved safe-stop obligations.

**Startup and net-export correction:** reduce unwanted signed export
immediately without an export-only permanent latch; verify supported
hardware response and resume only after fresh stable evidence.

**Manual OFF/restart:** preserve desired ON/OFF separately from actual
running state; request each reachable actuator's supported minimum and
verify actual readback, persisting failures across restart.

**Unsupported actuator:** do not infer device safety from a guessed
percentage or HA optimistic feedback. Do not claim unattended support.

## 07 — Deployment view

```text
Home Assistant Core (OS | Container | supported other installation)
  |-- SunEnergyXT EMS custom integration (Marketplace/HACS repository)
  |-- SunEnergyXT-BK upstream integration --> BK215 or BK215 Plus
  |                                           (+ compatible extension modules)
  |-- selected inverter integration(s) ----> inverter controls & telemetry
  |-- selected signed whole-site meter ----> one grid-power observation
  '-- Recorder / diagnostics --------------> traceable evidence
```

No second VM, dedicated cloud account, Proxmox node or Shelly device is a
required part of the software architecture. The installation's existing
backup facility covers rollback; validate clean installation, upgrades,
migration and rollback per supported HA version.

## 08 — Cross-cutting concepts

**Topology:** `Tower[head, capacity_wh, SoC, lower/upper, inverters[]]`
with stable device identifiers. Upstream extensions are part of the tower,
not separate EMS output devices. A tower may have multiple inverter
actuators when the upstream battery's physical ports permit them.

**Capability instead of brand detection:** for every inverter, distinguish
`detected`, `manually selectable`, `actuator implemented`,
`true ACK available`, `hardware-validated`. An unsupported profile does
not become validated because a writable number has a plausible name.

**Safety and freshness:** one normalized sign/unit convention; separate
timestamps for meter, SoC, command, ACK and measured AC/DC. Do not map
stale/unknown to 0 W. Limit dispatch by each tower's **validated**
electrical/firmware capability; no universal BK215 1,920 W rating for
all Plus/head/extension combinations.

**Configuration:** explicit safe defaults; user can amend battery/inverter
entities and ratings, not firmware register addresses. Preserve options
on reconfigure. Normal UI shows meaningful reasons instead of raw errors.

**Software delivery:** small Conventional Commit PRs, SemVer tags,
human-readable Keep a Changelog and measured compatibility evidence.

## 09 — Architecture decisions

- [ADR-0001 — One writer / independently verified feedback](../adr/0001-controller-and-feedback.md)
- [ADR-0002 — Ship validated core before economic planning](../adr/0002-core-release-scope.md)
- [ADR-0003 — Recoverable startup export](../adr/0003-startup-recovery.md)
- [ADR-0004 — Device-neutral towers and inverter capability adapters](../adr/0004-device-neutral-battery-and-inverter-boundaries.md)
- [ADR-0005 — Beta-first release milestone](../adr/0005-beta-first-core-scope.md)

Architectural decisions record context, alternatives, consequences and
status. Existing decisions are retained as history; ADR-0004 clarifies
their product-wide application instead of rewriting a site's test trace.

## 10 — Quality requirements and test strategy

- **Core tests:** zero/one/many towers, mixed BK215/BK215 Plus, B215
  Plus extension capacity and incompatible extension topology, 1..4
  distinct writable inverter mappings, multiple vendors and ratings,
  renamed IDs, missing source data, independent reserve and partial failure.
- **Adapter contract tests:** unit/step/min/max, a real fresh readback after
  write, timeout and loss of link, inverter restart (0%-to-positive), safe
  minimum and rejected unknown firmware/profile.
- **Negative tests:** no physical ACK must never be reported as success;
  no DC/AC data is not zero; one tower's error does not wrongly isolate
  another; stale net sample cannot authorize new positive output.
- **Hardware evidence:** validate separately **each claimed combination**
  of head/extension, inverter model, firmware, upstream HA integration
  and control profile. Test true grid/AC output, minimum/shutdown,
  settled ramp and restoration. Passing M200/M80 field tests alone is
  **not proof** for a Hoymiles/APsystems/TSUN combination.
- **Release evidence:** CI, reproducible site-independent fixtures,
  ≥24h meaningful field operation for the reference supported
  combination(s), and tested HACS/Marketplace upgrade/rollback.
  Mark all other combinations explicitly *unvalidated*.

## 11 — Risks and technical debt

1. The runtime currently implements direct register-53 ACK only for the
   specifically validated Solarman `deye_micro_53.yaml` profile; other
   selected `number` platforms can produce **HA-state-only feedback**.
   A vendor adapter with an independently trustworthy ACK mechanism is
   required before claiming unattended support.
2. Experimental `b18` still contains device-family-specific defaults
   such as a conservative legacy 1,920 W AC command budget. The DC
   telemetry latch is removed from the beta implementation; a higher
   Plus/tower limit must be explicitly configured and hardware-validated. See [#53](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/53).
3. There are no completed live tests across every vendor or Plus topology.
   Automatic discovery of the official BK Plus head is plausible through
   matching integration-owned unique-ID roles, but not itself hardware
   acceptance.
4. Forecast economics, physical transient attribution and installation
   rollback remain distinct unaccepted tasks. Avoid making them a hidden
   dependency for generic architectural documentation.

## 12 — Glossary

| Term | Meaning |
| --- | --- |
| **Head** | One BK215 or BK215 Plus with its own SoC, reserve and communication identity |
| **Extension** | B215 / B215 Plus module attached to a head; contributes storage, not an independent power actuator |
| **Tower** | One head and its compatible extensions, with configured total Wh |
| **Inverter actuator** | Configured independently writable HA power-limit entity, in W or % |
| **Hardware-compatible** | Manufacturer permits an electrical BK-to-inverter pairing; **not** proof of HA controllability |
| **EMS-validated** | Specific firmware/provider/write/readback/physical response and recovery accepted in tests |
| **ACK** | Fresh independent hardware confirmation that the actuator accepted a specific limit; not the HA number state |
| **Net import/export** | Signed whole-site meter (+ import, − export), summed across phases |
| **Safe OFF** | Lowest supported request plus verified ACK, or explicitly unresolved shutdown duty |
