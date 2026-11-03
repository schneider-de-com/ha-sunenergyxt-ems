# SunEnergyXT EMS v0.1.0b5 — experimental refactoring beta

This is an opt-in **experimental** Home Assistant EMS beta. The supervised M200-only test on 2026-11-03 recorded repeated phase-A export spikes and a 0%→10% restart cycle. No software-only test proves inverter AC behavior or physical compliance with BK215 DC limits. **Do not enable unattended inverter control until real hardware validation passes.**

## Requested features implemented

- **#35 — variable-gain bounded PID:** Optional selectable adaptive PID based on the JPS controller concept, with tunable Kp (small/large import and export), Ki, Kd, integral saturation protection, distinct-meter-sample time gating and diagnostic P/I/D values. Existing installations retain the previously agreed 20 W P-controller default until PID is intentionally enabled through Configure. Existing per-inverter rate/ACK, 5-second loop, 0–30 W deadband, 20/120/520 W correction bounds and main/support priorities remain.
- **#36 — 0→active startup hold:** A configurable 30-second hold protects each inverter against immediate normal correction after starting from its minimum limit. **Reserve, deliberate disable, invalid meter, DC fault and confirmed sustained export are not suppressed.** PID integration is frozen during the hold to prevent post-hold windup. A readback of the Deye percentage limit is not measured AC output.
- **#37 — feed-in after full charge:** Both originally agreed PV and household modes remain, with feed-in off by default. In PV/full-SoC mode, a user-configured current PV-input sensor can cap post-full-charge output to current PV input; if omitted, legacy release-to-rated-limit semantics remain. If configured input is stale/unavailable, normal demand-following fallback is used rather than guessing PV input.
- **#38 — EV3600 auto-charge:** Optional per-BK215 EV3600 mode switch and charging power sensor. An active auto-charge mode *and* fresh positive charging power stop only that BK215's available inverter outputs. An unreadable configured EV state fails closed for the affected BK215; other batteries with valid measurements continue. No EV3600 service commands are sent.
- **#39 — diagnostics:** Graphable Home Assistant controller diagnostic sensors for raw/effective grid watts, grid error, PID proportional/integral/derivative terms and total requested setpoint, with per-BK215 state and source readings. Configure an actual AC power sensor per inverter if available; an HA number setpoint is never described as measured AC power. English/German translations are provided.

## Upgrade and rollback

Install the tagged `v0.1.0b5` beta explicitly from the existing HACS custom repository after creating a Home Assistant backup. Do not add a second config entry. Review **Configure** to enable PID and select optional sources; unset sources are not guessed from names. Confirm current `switch.sunenergyxt_ems` state is preserved on upgrade and no competing automations write the same Deye or APsystems setpoints.

## Acceptance and remaining safety gates

The repository's required CI includes behavior-first RED/green tests, real HA entry/device/entity lifecycle, typed code, Ruff, Bandit, HACS and CodeQL validation, hardware-shaped replay and fault-negative tests. This checks software behavior only. A supervised measurement must still independently verify Deye M200 AC output, BK215 DC output and signed Shelly phase/grid response, especially after 0→active startup. M80 Solarman communication was unavailable on 2026-11-03. No stable/unattended production release is implied.

Implementation ideas were evaluated against [JPS BK215 Hybrid Controller](https://github.com/jps-hub/ha_bk215_hybrid_controller), MIT licensed. Our code is a separate implementation integrated with our existing safety contract, not a copy of JPS's entire controller.
