# SunEnergyXT EMS v0.1.0b4 — experimental hardware-validation beta

This is an opt-in beta, not an unattended-production release. **Do not turn on the full EMS controller until its real Deye/BK215 control and protection have passed a supervised test.**

## Fixed since v0.1.0b3

- **Legacy BK215 configuration upgrade:** Existing Home Assistant config entries created before the DC-output mapping existed now resolve the official `wifi_outputpower_total` sensor using the original SunEnergyXT device registry identity. No new config entry or entity-name guesswork is required. If the registered DC sensor is missing, invalid or stale, the EMS issues safe-minimum commands rather than increasing outputs.
- **Visible battery telemetry:** Each BK215 EMS status entity exposes its selected `dc_sensor` and the measured `dc_output_w` for diagnosing migration and device communication.
- **Offline during shutdown:** A Deye limit that was unavailable at manual disable is remembered during the running Home Assistant process; if it reconnects, the disabled EMS sends the previously missed 0% limit command and waits for a fresh readback. Do not mistake a successful service response for confirmation of real AC output. This pending retry is not yet persistent across a Home Assistant restart.

## Grounded real-system validation

On 2026-11-03 21:03 Berlin time, with all other EMS automations disabled and the new EMS switched off, a supervised M200 **0% → 5% → 0%** command was acknowledged as an HA limit. BK215(1) later measured 102 W DC output and returned to zero by 21:05. During the 5% interval, the Shelly Pro 3EM recorded a transient signed grid total of **−452.864 W**, driven by phase A (−772.5 W). Independent, synchronous Deye M200 AC measurements were unavailable, so the cause **is not proven**; the physical 5% AC limit and absence of export excursions were **not validated**. M80 Solarman was in `setup_retry` and unavailable, so the dual-inverter end-to-end test was **not performed**.

## Installation and remaining gates

Install the `v0.1.0b4` prerelease explicitly from this HACS custom repository. Back up Home Assistant, keep `switch.sunenergyxt_ems` off, and confirm existing config entry ID is unchanged. The M80 communication fault must be resolved before testing coordinated control. Re-run supervised AC/DC/grid measurements, reserve cutoff, Solarman outage/reconnect and shutdown-ACK tests before any stable, unattended release.

Source issues: #27 (transient export/oscillation), #28 (HACS beta metadata), #31 (DC safety), #32 (unreachable M80 shutdown). PR #33 implements the software fixes above. The manufacturer's BK215 1,920 W limit is a **DC** output constraint; AC setpoints cannot establish compliance.
