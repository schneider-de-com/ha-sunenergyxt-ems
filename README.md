# SunEnergyXT EMS

**An experimental Home Assistant energy controller for SunEnergyXT BK215 and BK215 Plus systems.** It uses a site's measured grid import/export to adjust compatible microinverter power limits. This is an independent community project, not an official SunEnergyXT integration.

> **Beta software — not approved for unattended hardware control.** Hardware behaviour, command acknowledgements and safe shutdown must be verified for each inverter/profile. See [compatibility](docs/device-compatibility.md).

## What it does

- Follows one **signed, whole-site grid power** sensor: positive = grid import; negative = export. No specific meter brand is required.
- Coordinates multiple independent BK215/BK215 Plus **head units**, respecting each storage tower's own discharge reserve. B215/B215 Plus extensions belong to a head unit, not to a separate controlled tower.
- Maps up to **four configurable power-limit entities per head** (W or %), subject to real hardware limits and independently validated actuator support.
- Offers basic demand-following, optional adaptive PID and configurable no-export / after-full-charge feed-in settings. **PV feed-in can export stored battery energy.**
- Shows status and diagnostics separately for requested limits, HA-reported limits, measured AC power (if available) and per-storage state. It attempts a safe minimum and records unresolved shutdown commands when disabled.

Forecast planning, demand learning and economic comparison are **experimental**, not verified savings or stable control features.

## Before installation

You need an operational Home Assistant instance, a [SunEnergyXT-BK integration](https://github.com/SunEnergyXT/SunEnergyXT-BK) exposing SoC and reserve values for each head (or a verified manual mapping), a signed grid-power sensor in W/kW, and writable inverter power-limit entities through their own upstream integration.

**A writable Home Assistant number entity is not proof of safe EMS compatibility.** Direct register-53 hardware feedback is implemented for a specifically checked Deye/Solarman profile; Hoymiles, APsystems, TSUN and other models require their own control and readback verification. Check the [device support matrix](docs/device-compatibility.md) before attempting control.

The software has CI coverage on Home Assistant 2026.10.4 and 2026.11.1; that is not a physical hardware certification.

## Install and set up

1. **Back up the installation** and disable any other automation/controller writing the intended inverter limit entities. There must be **one writer only**.
2. Add this GitHub repository as a custom **Integration** in Home Assistant **Marketplace** (2026.11+) or **HACS** (earlier supported releases). Install a published [beta release](https://github.com/schneider-de-com/ha-sunenergyxt-ems/releases) and restart Home Assistant if prompted. The integration is not in the default catalog.
3. Go to **Settings → Devices & services → Add integration → SunEnergyXT EMS**. Select each independent BK head, its inverter limit entities and the signed site meter. Supply inverter rated watts if missing. Verify the actual permitted head/extension combination.
4. In **Configure**, check each lower/upper SoC, total storage capacity **including extensions (Wh)**, the **combined inverter AC setpoint budget per head (W)**, inverter mappings, feed-in mode and optional AC/PV measurements. Existing configurations retain a conservative 1,920 W combined setpoint budget until explicitly changed; do not enter a higher limit without checking the manufacturer's rating. Use **Reconfigure** to change assigned heads, inverters or the meter.

**Important:** A new EMS configuration starts **disabled**. Review the mapping and status before explicitly enabling its EMS switch for a supervised test. Disable other controllers first; installing or upgrading the integration never overrides an existing enabled/disabled choice.

## Operate and troubleshoot

Under **Settings → Devices & services → SunEnergyXT EMS**, use the controller's **EMS switch** to enable or disable regulation. The device also offers PID controls, grid/limit diagnostics and storage-specific status; **Configure** contains optional measurement and strategy settings.

A successful Home Assistant service call or a displayed 0% limit **does not prove zero physical AC output**. Missing meter/SoC values, unavailable inverters and unconfirmed shutdowns must be investigated rather than interpreted as safe operation. Some Deye devices show nonfatal fault states at their 0% limit.

For troubleshooting, check the EMS status, signed net power, each inverter's reported limit and actual hardware readback, individual reserve/SoC and pending shutdown errors. Use an existing backup and the last verified release for a controlled rollback; do not edit Home Assistant's internal storage files.

## Documentation

- [Supported and unvalidated equipment](docs/device-compatibility.md)
- [Architecture (arc42)](docs/architecture/README.md) and [ADRs](docs/adr/)
- [Changelog](CHANGELOG.md), [release policy](docs/releasing.md) and [GitHub releases](https://github.com/schneider-de-com/ha-sunenergyxt-ems/releases)
- [Detailed configuration / forecast limits](docs/horizon-planning.md) and [contributing / Definition of Done](CONTRIBUTING.md)

**License:** [Apache-2.0](LICENSE).
