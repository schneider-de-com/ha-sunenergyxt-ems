# Deye AC register investigation (Refs #53, #92)

Status: investigation on 2026-11-13; no alternative fast AC-power source is
validated. The user requested checking other registers before considering
additional metering. This note changes no profile, controller or hardware setting.

## Source boundaries

- The user supplied `deye_micro_53.yaml`: default polling is 5 seconds; only the
  Info group overrides it to 3600 seconds. Power uses holding registers
  `0x0056/0x0057` (decimal 86/87), rule 3 and scale 0.1 W. This is supplied text,
  not an independent readback of the loaded file.
- The installed Solarman version is reported as 25.08.16. Its tagged
  [microinverter profile][profile] uses the same Power registers and scale. Its
  [parser][parser] assembles the listed words least-significant first. The
  [current upstream profile][current-profile] also retains 86/87; this does not
  mean that newer upstream code is installed or tested here.
- The profile references [Deye Modbus protocol V118][protocol]. PDF page 5 says
  it covers micro, string and storage inverters and that unsupported/reserved
  registers return zero. Page 12 reuses addresses across product families.
  A label on that page does not establish support on these microinverters.
- HA reports device type Microinverter, protocol 3.2, control-board firmware
  `0325-0103-0700` and communication-board firmware `1426-120C` for both devices.
  These reported identities are not hardware certification of this register map.

## Candidates and their limits

| Registers (decimal) | Source and meaning | Eligibility as an AC-power replacement |
| --- | --- | --- |
| 86/87 | User profile and tagged upstream: AC Power, low/high word, 0.1 W | Existing source. Matching raw/HA values do not establish fresh physical acquisition. |
| 80–85 | Mixed-family PDF p.12: display/input/apparent-power candidates | Zero in the passive reads below. No microinverter-specific replacement mapping established. Apparent power is VA, not active W. |
| 88/89 | Mixed-family PDF p.12: reactive-power candidate | Zero in the passive reads; reactive power is not active power. |
| 73 and 76 | Tagged micro profile: voltage 0.1 V and current 0.1 A | Multiplication gives apparent power, not active power without a validated power factor and aligned acquisition. The 0.1 A step is about 23 VA at 230 V. |
| 91–93 | Generic PDF p.13 labels differ from observed numeric patterns | Unresolved model-dependent semantics; do not assign the generic power-factor label to 93. |
| 109–116 | Tagged micro profile: four DC voltage/current pairs | DC input power; conversion losses and acquisition timing prevent treating it as measured AC output. |
| 60 and 63/64 | Tagged micro profile: daily and cumulative energy, 0.1 kWh | Potential coarse energy cross-check after counter/reset validation; not instantaneous power. One 100 Wh count represents 1200 W averaged over 5 minutes or 200 W over 30 minutes. |

A [separate community implementation][community] for **SUN-M160G4** uses 86 for
AC power, 91 for voltage and 93 for frequency. It is a clue to the unexpected
91–93 values, not manufacturer documentation or validation for these M200/M80
devices. It still provides no alternative AC-power register. Its scripts were
read, not executed or installed.

## Passive observations supplied by the live investigation

The following are holding-register responses from the existing Solarman service,
without stimulus commands. Times are UTC and identify request starts, not a
synchronized physical sampling instant. An all-zero response cannot distinguish
unsupported registers from a genuine zero reading.

| Request start | Device | Raw observations |
| --- | --- | --- |
| 2026-11-13 16:53:21 | M200 | 79=5000; 80–85=0; 86=3476; 87–89=0 |
| 2026-11-13 16:53:21 | M80 | 79=5000; 80–89=0 |
| 2026-11-13 16:54:29 | M200 | 73=2260; 74=6492; 75=0; 76=15; 77=6686; 78=0; 79=5000; 80–85=0; 86=3476; 87–89=0; 90=4410; 91=2291; 92=2281; 93=4998 |
| 2026-11-13 16:54:29 | M80 | 73=2250; 74–78=0; 79=5000; 80–89=0; 90=3350; 91=2303; 92=2303; 93=4981 |

At the first observation, M200 HA Power was 347.6 W, matching
`(3476 + (0 << 16)) * 0.1`. This verifies decoding for that response, not its
physical freshness. No candidate in 80–89 supplied a distinct nonzero signal.
The differing 91–93 values must remain raw until the model-specific mapping is
established. No register write, profile substitution or freshness relaxation
follows from this table.

### User-requested control-register readback: 40, 53 and 55

The user then explicitly requested these three registers. Read-only queries
between 2026-11-13 16:56:24 and 16:56:51 UTC returned:

| Register | M200 raw | M80 raw | What the observation establishes |
| --- | --- | --- | --- |
| 40 (`0x0028`) | 100 | 100 | A separate control-register value. The mixed-family PDF calls this active-power regulation with `0.1%/1%`; the exact installed-firmware scale is unresolved. Neither 10% nor 100% is verified from this read alone. |
| 53 (`0x0035`) | 1660 | 0 | The supplied active profile's 0.01 scale gives M200 16.6%, matching its simultaneous HA limit and nominal requested 332 W; this is command/readback agreement, not measured AC output. |
| 55 (`0x0037`) | 10000 | 10000 | Equal raw values, with no independently validated installed-firmware interpretation. The generic PDF labels 55 “Factory only”; do not convert a different model's trim interpretation into permission to change it. |

These are **control** registers, not an alternative AC measurement channel. The
generic PDF labels 53 “Hardware Matching”, conflicting with the supplied working
microinverter profile. That conflict reinforces the need for model/firmware
evidence rather than transferring its generic control table. No write to 40, 53
or 55 was performed; the existing EMS remained the only actuator writer, with
`last_error=null` and no shutdown/deferred obligation at this snapshot.

## Polling is distinct from acquisition

Solarman's [coordinator][coordinator] uses `always_update=False`; identical data
can suppress entity callbacks. Its [entity implementation][entity] writes cached
coordinator values, so HA value-change time is not a device sampling timestamp.
The maintainer [explicitly reports][cadence] that microinverters can answer with
the same old data for five minutes, sometimes one minute, regardless of request
interval. That supports the observed limitation but does not isolate this
installation's logger versus inverter firmware behavior.

Next: compare bounded repeat reads and independently timed existing grid/DC/AC
observations, establish any model-specific candidate's units and actual cadence,
and keep missing coverage explicit. Do not use U×I, DC power, a returned zero or a
cached timestamp to manufacture complete household-demand history. A slower
energy cross-check and transient safety validation are different questions.
The seven-day feature acceptance window has not started.

[protocol]: https://github.com/user-attachments/files/16597960/Deye.Modbus.protocol.V118.pdf
[profile]: https://github.com/davidrapan/ha-solarman/blob/v25.08.16/custom_components/solarman/inverter_definitions/deye_micro.yaml
[parser]: https://github.com/davidrapan/ha-solarman/blob/v25.08.16/custom_components/solarman/parser.py#L145-L195
[coordinator]: https://github.com/davidrapan/ha-solarman/blob/v25.08.16/custom_components/solarman/coordinator.py#L18-L21
[entity]: https://github.com/davidrapan/ha-solarman/blob/v25.08.16/custom_components/solarman/entity.py#L41-L61
[current-profile]: https://github.com/davidrapan/ha-solarman/blob/b5085f5e61b93bd0840e4cbc058a317cbc7c321d/custom_components/solarman/inverter_definitions/deye_micro.yaml#L800-L811
[community]: https://github.com/machtnichts/ha-power-dashboard/blob/0882ae93787f669a1f619b153f3baadffd3b9db5/powerdash/deye_pv.py#L84-L105
[cadence]: https://github.com/davidrapan/ha-solarman/issues/84#issuecomment-3783283038
