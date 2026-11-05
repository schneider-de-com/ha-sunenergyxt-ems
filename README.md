# SunEnergyXT EMS for Home Assistant

> **Experimental MVP beta 0.1.0b7. Device-specific commissioning evidence and remaining hardware limits are recorded in the release notes.**
>
> Independent community integration. Not affiliated with SunEnergyXT or Home Assistant.

SunEnergyXT EMS controls existing inverter power-limit entities to follow household demand.
It does **not** speak directly to batteries or inverters.

## MVP features

- Guided setup: **BK215 batteries → inverters → grid power meter**.
- Auto-detect the existing [SunEnergyXT BK integration](https://github.com/SunEnergyXT/SunEnergyXT-BK) and suggest its SoC and charge/discharge limits. Manual corrections are possible.
- One or two independently controllable microinverters per BK215, in watts or percent.
- One grid meter for all batteries and SoC-based main/support coordination.
- Battery reserve protection, grid-meter failure fallback, and manual disable with per-inverter shutdown readbacks and visible failures.
- Optional household-load or upper-SoC-triggered PV-generation feed-in modes; **feed-in is off by default**. The PV mode can export stored energy.
- Optional tunable adaptive PID with bounded I/D terms; existing systems default to the established proportional regulator.
- Configurable 30-second inverter startup settling hold; emergency reductions are not delayed.
- Optional per-BK215 fresh PV input limit after full charge and optional EV3600 auto-charge protection (no EV charge writes).
- PID, grid, command and actual-power **diagnostics**, with independent AC sensors selected explicitly in Configure.

## Requirements

- A current Home Assistant version using Python 3.14.
- At least one configured SunEnergyXT BK215 battery and a readable SoC sensor.
- A *writable* Home Assistant `number` power limit for every inverter.
- A **signed, real-time grid-power sensor** in W or kW. Positive must mean import; negative means export. Reverse the sign in setup if needed.
- Only one controller may write a given inverter power-limit entity.

## Install with Home Assistant Marketplace or HACS (experimental)

> **Experimental beta.** Take a Proxmox snapshot or Home Assistant backup first.
> This is a custom repository, not a default Marketplace catalog entry.

1. On **Home Assistant 2026.11 or later**, go to **Settings → Marketplace**. Earlier versions (including 2026.10.4) use **HACS**.
2. Add the custom GitHub repository `https://github.com/schneider-de-com/ha-sunenergyxt-ems` (type **Integration**). Select the published `v0.1.0b7` prerelease (enable beta versions where supported); do not use a short Git SHA as a branch name. The Marketplace requires a GitHub account connection for custom repositories.
3. Install **SunEnergyXT EMS** from the Marketplace or HACS. Restart Home Assistant if requested. Adding files does not start inverter control.
4. Ensure your inverter controls and grid meter are ready before setup.
5. For a live setup: **turn off all existing automations writing to the selected inverter power-limit entities before completing the EMS config flow.** Go to **Settings → Devices & services → Add integration → SunEnergyXT EMS**.
6. Choose your BK215 devices, detected inverter controls and signed grid power meter. If nominal inverter watts cannot be read reliably, enter them once.

**Important:** Completing the config flow starts the EMS automatically when current, valid measurements exist. There is no read-only simulation mode in the MVP. **Do not complete the flow while another controller is active.** Only one controller may write any inverter's power limit.

### Where to find the JPS-derived features

In **Settings → Devices & services → Integrations → SunEnergyXT EMS**, open the **SunEnergyXT EMS** controller device:

- **Switches:** EMS on/off, feed-in after full charge, and **Adaptive PID**. New/old installations keep existing PID setting; **EMS remains OFF unless you explicitly switch it on**.
- **Number controls:** startup hold (default 30 seconds), variable PID Kp min/max/export, Ki and Kd; they persist through Home Assistant and use the same bounds as Configure.
- **Sensors:** live signed grid raw/filtered power, error, PID P/I/D, requested total, and **startup hold remaining**. When EMS is off, past signed grid readings are not displayed as live values.

Each detected **BK215 (EMS)** child device has its own **EV3600 auto-charge** and **full-charge bypass** state sensors. The per-inverter requested limit, reported HA power limit and actual measured AC power are *separate* sensors. AC is unknown until a fresh AC-power entity is selected: a reported Deye limit is **never** measured power.

Use **Configure** to select the EV3600 mode switch + charging-power sensor **as a pair** for the relevant battery, an optional fresh PV input and an independently measured AC source. An unconfigured EV3600 state shows **Not configured**, not a false 'not charging' confirmation. Existing charge SoC mappings can now be corrected using **Battery N: upper SoC source** or an explicit **Battery N: upper SoC (%)**. An explicit numeric override takes precedence over an unchanged existing source; choosing a different source takes precedence over the previous numeric default. **Changing the EMS upper mapping does not change the BK215 hardware charging ceiling.** Do not configure a threshold the battery cannot reach. Feed-in remains OFF by default and may export stored energy.

Use **Reconfigure** to change assigned batteries, inverters and the shared grid meter.

The new Marketplace uses the same `hacs.json` and GitHub release layout as HACS. The integration is **not yet published to the default catalog**.

## Safety and limitations

- Home Assistant service-call success does not prove that a disconnected inverter changed its physical output.
- Disabling the EMS sends a minimum-safe command to **each reachable configured inverter** and requires fresh HA limit readbacks before claiming confirmed shutdown. A limit readback still does not prove actual AC zero injection. Unreachable inverters cannot be stopped reliably.
- If the grid sensor fails, the controller briefly holds and then reduces reachable inverters. It resumes after several fresh samples.
- A bad SoC reading disables only the affected battery; healthy batteries can continue. A BK215 DC-output reading above 1,920 W latches a protective shutdown; the trip is saved across HA restarts. To reset it, turn off EMS and use **Configure → Reset DC safety trip** only with recent BK215 DC output at or below 100 W. This is a software safety fallback, not a measurement certification.
- Some Deye inverters report a known nonfatal diagnostic fault at 0% limit.
- Feed-in after full charge may export stored battery energy. Use it knowingly.
- A physically failed controller or Home Assistant shutdown cannot issue a final safety command.
- Current automated tests are not a replacement for real inverter and BK215 hardware validation. The BK215 combined **DC** 1,920 W limit is **not** equivalent to the sum of AC inverter setpoints. Avoid unattended live operation until actual DC/AC behavior is confirmed (#31).

## Development

The [six MVP issues](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues)
define the scope. See the [Definition of Done](CONTRIBUTING.md#definition-of-done).
Every commit should reference its relevant issue.

## License

[Apache License 2.0](LICENSE).

Beta details and staged hardware validation: [release notes](RELEASE_NOTES_0.1.0b7.md). An installed entry intentionally set to **off** stays off after an update; do not enable it merely because the package installed successfully.
