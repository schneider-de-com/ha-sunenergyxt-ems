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

## Development installation

This is a development build, **not yet a released HACS integration**.

1. Copy `custom_components/sunenergyxt_ems/` into your Home Assistant configuration.
2. Restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration → SunEnergyXT EMS**.
4. Select the BK215 devices, the inverter limit entities, and the grid sensor. Enter inverter rated watts only when not detected.
5. **Disable your old inverter-control automations before completing setup.** The new EMS starts actively controlling only when valid current measurements are available.

The integration creates **EMS control**, **Feed-in after full charge**, and **EMS status** entities.
The control switch and options flow preserve settings across restarts.

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
