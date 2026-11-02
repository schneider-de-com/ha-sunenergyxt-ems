# SunEnergyXT EMS for Home Assistant

> **Pre-alpha. Not production-ready. No stable release yet.**
>
> Independent community integration. Not affiliated with SunEnergyXT or Home Assistant.

SunEnergyXT EMS controls existing inverter power-limit entities to follow household demand.
It does **not** speak directly to batteries or inverters.

## MVP features

- Guided setup: **BK215 batteries → inverters → grid power meter**.
- Auto-detect the existing [SunEnergyXT BK integration](https://github.com/SunEnergyXT/SunEnergyXT-BK) and suggest its SoC and charge/discharge limits. Manual corrections are possible.
- One or two independently controllable microinverters per BK215, in watts or percent.
- One grid meter for all batteries and SoC-based main/support coordination.
- Battery reserve protection, grid-meter failure fallback, and safe manual disable.
- Optional feed-in after full charge, **off by default**.

## Requirements

- A current Home Assistant version using Python 3.14.
- At least one configured SunEnergyXT BK215 battery and a readable SoC sensor.
- A *writable* Home Assistant `number` power limit for every inverter.
- A **signed, real-time grid-power sensor** in W or kW. Positive must mean import; negative means export. Reverse the sign in setup if needed.
- Only one controller may write a given inverter power-limit entity.

## Install with HACS (experimental)

> **Pre-alpha test build.** Take a Proxmox snapshot or Home Assistant backup first.
> This is a custom HACS repository, not a default HACS catalog entry.

1. Open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/schneider-de-com/ha-sunenergyxt-ems` with type **Integration**.
3. Find **SunEnergyXT EMS** in HACS and choose **Download**.
4. Restart Home Assistant after the download. Adding files does not start inverter control.
5. For a live setup: **turn off all existing automations writing to the selected inverter power-limit entities before completing the EMS config flow.** Go to **Settings → Devices & services → Add integration → SunEnergyXT EMS**.
6. Choose your BK215 devices, detected inverter controls and signed grid power meter. If nominal inverter watts cannot be read reliably, enter them once.

**Important:** Completing the config flow starts the EMS automatically when current, valid measurements exist. There is no read-only simulation mode in the MVP. **Do not complete the flow while another controller is active.** Only one controller may write any inverter's power limit.

The integration exposes a global EMS switch, optional feed-in switch, global status sensor and individual BK215 status devices. Use **Configure** to change operating settings and **Reconfigure** to change assigned batteries, inverters and grid meter.

## Safety and limitations

- Home Assistant service-call success does not prove that a disconnected inverter changed its physical output.
- Disabling the EMS sends one minimum-safe limit to reachable inverters. Unreachable inverters cannot be stopped reliably.
- If the grid sensor fails, the controller briefly holds and then reduces reachable inverters. It resumes after several fresh samples.
- A bad SoC reading disables only the affected battery; healthy batteries can continue.
- Some Deye inverters report a known nonfatal diagnostic fault at 0% limit.
- Feed-in after full charge may export stored battery energy. Use it knowingly.
- A physically failed controller or Home Assistant shutdown cannot issue a final safety command.
- Current automated tests are not a replacement for real inverter and BK215 hardware validation.

## Development

The [six MVP issues](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues)
define the scope. See the [Definition of Done](CONTRIBUTING.md#definition-of-done).
Every commit should reference its relevant issue.

## License

[Apache License 2.0](LICENSE).
