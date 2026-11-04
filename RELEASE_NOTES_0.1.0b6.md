# SunEnergyXT EMS v0.1.0b6 — user-visible JPS refactoring beta

**Experimental beta — not validated for unattended inverter operation.** v0.1.0b5 introduced PID, startup hold, post-full-charge PV bypass, EV3600 protection and diagnostic logic, but called those five user stories done prematurely: critical controls were buried in Configure, existing upper SoC mappings were not editable there, and inverter/tower diagnostics were missing as discrete HA entities.

## User-visible parity corrections (PR #42)

- **#35: PID** now has a native HA on/off switch and editable Kp low/high/export, Ki, Kd number entities on the EMS controller device; parameters remain available under Configure. This is **not** evidence of stable closed-loop regulation; see #27.
- **#36: Startup hold** has a native seconds number and a separate hold-remaining diagnostic sensor; a 30-second hold was observed on the real M200 during a previous controlled test.
- **#37 / #41: Full-charge bypass** now exposes individual BK215 bypass-status sensors, and existing installations can edit each BK215's stored upper SoC source or explicit threshold through Configure. This edits EMS policy mapping only; it cannot change the battery's physical charge ceiling. Existing feed-in strategy, hysteresis and default OFF are preserved.
- **#38: EV3600 protection** has an explicitly translated per-BK215 status sensor showing Not configured / Idle / Charging / Unavailable, while existing optional auto-charge switch + charge-power configuration and fail-safe logic remain unchanged. No EV3600 hardware was connected during validation, so real charge detection remains unverified.
- **#39: Diagnostics** now distinguish each inverter's EMS requested watts, reported HA power-limit watts and independently measured AC watts (unknown unless configured and fresh). The control device exposes PID and startup state; both BK215 devices expose their own EV3600/bypass states. Signed meter readings from before disabling EMS are not displayed as if live.
- English/German translations and real HA entry/device/entity lifecycle regression tests are updated.

## Upgrade and important safety limitation

Upgrade the **existing** HACS / Marketplace custom-repository installation to tagged `v0.1.0b6` only after an ordinary HA backup. Keep `switch.sunenergyxt_ems` OFF; do not add a duplicate integration. Review the new controls under the existing EMS Controller device and each BK215 EMS device. No new EV3600 or physical AC sensors are invented or automatically assigned, no battery charging limits are modified, and existing PID/operating mode settings remain unchanged.

Live hardware test of v0.1.0b5 on 2026-11-04 observed controller requests 0.7–11.6% and 12/32 exported signed grid samples despite +20 W target. Also an M80 was found at 100% after EMS had been disabled, on reconnect; it was manually returned to zero. **Issue #27 (closed-loop regulation), #32 (persistent safe shutdown/reconnect) and #31 (DC/AC hardware safety) remain blockers for unattended operation.** This beta does not claim those issues are solved or that EV3600/Bypass passed a real load test.

The refactoring uses JPS Hybrid Controller as a functional reference; our code is a separate implementation under Apache-2.0, not an unqualified copy or a claim of identical real-world behavior. See [README](README.md) for where to find each feature and [CONTRIBUTING](CONTRIBUTING.md) for RED/GREEN/real-HA acceptance gates.
