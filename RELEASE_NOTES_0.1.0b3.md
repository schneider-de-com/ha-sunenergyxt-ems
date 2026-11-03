# SunEnergyXT EMS 0.1.0b3 — experimental MVP beta

This is an **opt-in prerelease** of the independently developed SunEnergyXT EMS integration for Home Assistant. It is **not verified for unattended operation with physical BK215 or Deye hardware**. Do not enable the controller until you have confirmed actuator feedback, reserve boundaries, battery output and the inverter firmware behavior in a supervised test.

## MVP behavior

- Guided BK215, inverter-limit and signed-grid-meter selection, with two inverters per BK215 and configurable settings.
- Shared, feedback-gated household-load regulation. A successful Home Assistant service call alone is not treated as a confirmed inverter response; subsequent corrections require an actuator readback and newer, settled grid measurement.
- Per-battery SoC reserve protection and limited fallback for missing grid input.
- Explicit shutdown that attempts to set **each reachable inverter** to its safe minimum, displays outstanding acknowledgements, and reports communication failures rather than claiming success.
- Two user-selected feed-in behaviors. **Household demand** follows house load independently of maximum SoC. **PV generation** can release the inverter power limit after the upper SoC is reached; configurable SoC hysteresis stops the override. This mode can export stored battery energy.
- Monitored BK215 DC-output telemetry (official t34 sensor, if mapped), with a latched software trip for readings above the documented 1,920 W combined DC-output rating. **The AC limit alone does not establish the physical DC power.**

## Installation and upgrade

Add this repository as a custom integration in HACS / Home Assistant Marketplace. Install the tagged `v0.1.0b3` beta release (enable prereleases if necessary), restart Home Assistant if requested, and review the existing config entry. Do not add a duplicate integration. Verify that `switch.sunenergyxt_ems` remains **off** after upgrading an intentionally disabled installation.

Before any supervised hardware test, take a Home Assistant backup or Proxmox snapshot; ensure all competing automations controlling the same inverter number entities are off. Use the M80 at low power first, check actual AC power **and** BK215 DC output, then test the M200. Compare with the signed Shelly Pro 3EM grid reading. A `0%` power-limit entity and a successful service response are not sufficient proof of physical zero injection.

## Known limitations and release gates

- The user's BK215(1) reported **2,077 W** DC at a time when an M200 AC-limit command was 1,920 W. The cause, time alignment and physical measurement semantics remain unverified. See #31. The added software trip is not a replacement for the BMS or a hardware-validation result.
- A local Solarman readback can lag. Confirm actual M80/M200 AC power with new samples; no hardware-independent software test proves electrical isolation.
- HACS previously requested invalid commit-as-branch URLs (404). The versioned release/tag is intended to prevent the SHA-only fallback; a complete in-place HACS update must still be tested (#28).
- No physical runtime/energy-savings claim is made from the replay, mocks, CodeQL or CI checks.
- Advanced dynamic tariff, PV forecast, seasonal optimization and a permanent simulation mode are intentionally outside the original MVP.

Related: #6, #22, #27, #28, #30, #31, #32 and PR #29.
\n