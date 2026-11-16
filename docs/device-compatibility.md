# Device compatibility and validation matrix

This page distinguishes three different claims:

1. **Manufacturer electrical compatibility:** a BK head can be connected
   to a specified microinverter according to SunEnergyXT.
2. **Home Assistant entity compatibility:** an upstream integration exposes
   a readable, fresh SoC and a writable power-limit `number` for each
   inverter.
3. **EMS runtime/hardware acceptance:** the exact model/firmware/profile
   has a verified limit ACK, safe minimum, ramp/settling and recovery.
   **Only the third claim permits unattended EMS support.**

Do not interpret the first two as the third.

## Storage towers

| Head (one EMS tower) | Extensions attached to same head | Valid topology | HA source |
| --- | --- | --- | --- |
| **BK215** | 0–3 × B215 and/or B215 Plus | Manufacturer allows mixed first-/Plus-generation extensions; existing head still limited to three | [SunEnergyXT-BK](https://github.com/SunEnergyXT/SunEnergyXT-BK), legacy `sunEnergyXT` or current `sunenergyxt_bk` |
| **BK215 Plus** | 0–7 × **B215 Plus** | **B215 (first generation) must not be connected to BK215 Plus** | Same official upstream plugin |
| **B215 / B215 Plus alone** | — | **Not** a standalone EMS tower (no independently dispatchable head/inverter output) | No separate EMS battery |

Sources: [SunEnergyXT BK215 Plus product / compatibility](https://www.sunenergyxt.com/details-speichersysteme-bk215-b215-plus),
[SunEnergyXT BK215/BK215 Plus HA guide](https://www.sunenergyxt.com/entdecken/steuerung/home-assistant/how-to-integrate-your-BK215-BK215Plus-with-home-assistant).

The official SunEnergyXT HA guide currently specifies **BK215 firmware ≥1.5.7**
and **BK215 Plus firmware ≥4.0.3**, with battery local communication
enabled. These are requirements for the *upstream battery integration*;
they do not certify this EMS or specific inverter combinations.

Every head has its **own** SoC, lower reserve, upper limit, charging
observations and total configured capacity (Wh). Each extension increases
the Wh of **its head**, rather than adding another independently commanded
battery. Never auto-assume a fixed 2,150/4,300/6,450 Wh tower based on an
example configuration.

## Manufacturer-supported microinverter pairings

Manufacturer's **BK215 Plus hardware** list:

| Brand | Manufacturer's listed model/family | Electrical pairing | EMS limit + independent hardware ACK |
| --- | --- | --- | --- |
| Deye | SUN-M(60–200) G3-EU-Q0 / G4-EU-Q0; SUN800G3-EU-230 | Listed | **Conditional**: direct ACK is implemented only for the specifically checked Solarman `deye_micro_53.yaml` profile; other firmware/models need validation |
| Hoymiles | HM-600-2T, HM-800-2T, HMS-600-2T, HMS-800-2T | Listed | **Not implemented/validated** as a vendor-specific ACK adapter |
| APsystems | EZ1-M | Listed | **Not implemented/validated** as a vendor-specific ACK adapter |
| TSUN | G3 | Listed | **Not implemented/validated** as a vendor-specific ACK adapter |

Source: [official SunEnergyXT BK215 Plus compatibility table](https://www.sunenergyxt.com/details-speichersysteme-bk215-b215-plus).
Check the manufacturer's current list and device revisions before purchasing
or wiring hardware. Hardware compatibility and EMS-controlled grid feeding
are distinct concepts. Do not infer that every model exposes the same local
write protocol or can reach an actual zero-watt output.

**SunEnergyXT 500/500 Pro:** a distinct AC/DC architecture with its own
official [Home Assistant integration](https://github.com/SunEnergyXT/SunEnergyXT-500-Series).
It is not automatically a BK tower or a supported EMS inverter actuator.
Treat any future integration as a separate, validated capability.

## Current software status (experimental 0.1.1b18)

- **Already generic:** the controller accepts arbitrarily named BK devices,
  their stable SoC/reserve roles, multiple independent towers, configurable
  `number` inverter limits in percent or watts, and a generic signed
  W/kW site power meter.
- **BK215 Plus recognition:** the upstream manufacturer uses the **same
  SunEnergyXT-BK integration** for BK215 and BK215 Plus. The EMS source
  matching is based on upstream unique-ID roles and HA device identity, not
  the text `BK215` in an entity name. This allows candidate discovery when
  Plus exposes those roles. Plus runtime/hardware acceptance is **pending**.
- **Current tower AC budget:** an existing entry retains a conservative 1,920 W combined inverter setpoint budget until a user configures an evidenced head-specific limit in Configure. This is **not** an inverter AC measurement or a BMS/DC trip threshold.
- **Current inverter constraint:** a recognised writable `number` can be
  selected manually, even if the upstream vendor is not Deye. **This does
  not certify support.** The current generic branch confirms HA state only;
  only the Deye-specific path tries a direct Modbus register-53 read.
- **Recommended operational posture:** do not activate unattended control
  on an unverified inverter/provider/profile solely because the setup wizard
  accepted an entity. Safe startup, shutdown and readback must first be
  demonstrated.

## Required capability contract before claiming a vendor supported

For each firmware/provider combination, record:

- Stable device/limit entity identity and **independent** write ownership
- Rated AC power, command unit, minimum, maximum, step and rounding
- Fresh connection/voltage truth, known startup behaviour and safe minimum
- How to **read back the real accepted limit**, with freshness/timeout and
  what can be verified when the device is unavailable
- Independent measured AC (if available) and raw signed grid response
- Per-tower output limit and any extension-specific electrical restriction
- Tested manual OFF, power loss/reconnection, zero-to-positive, down-ramp,
  high import, net export, missing telemetry and recovery
- Firmware versions, upstream HA integration versions, test dates and
  exact compatibility limits

Compatibility status progresses **discovered → mapped → bench-tested →
supervised-field-validated → releasable**. New names from the same hardware
family do not inherit hardware acceptance automatically.

## Representative configuration-independent scenarios

1. BK215 with B215; one Deye supported profile, single site meter.
2. BK215 Plus with several B215 Plus expansions; one separately validated
   inverter profile, correctly entered total Wh.
3. Mixed BK215 and BK215 Plus towers; different SoC reserves; one head may
   become unavailable without stopping the other.
4. BK215 Plus with multiple independently writable inverter outputs;
   limit allocation and combined tower cap must respect *actual* hardware
   restrictions, not the sum of nameplate inverter maxima.
5. Hoymiles / APsystems / TSUN selection: detection and UI alone must
   **not** be reported as a verified inverter ACK or stable support.

Keep the exact reference test equipment and incidents in GitHub issues,
fixture data and release evidence, rather than making it a requirement to
own one specific household system.
