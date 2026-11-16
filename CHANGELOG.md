# Changelog

Human-readable changes follow [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html). Version tags are immutable.

## [Unreleased]

No additional changes have been accepted since `v1.0.0-beta.2`. Continue to document verified changes here before the next prerelease.

## [1.0.0-beta.2] - 2026-11-16

**Experimental prerelease.** These changes were merged after `v1.0.0-beta.1` and included in the immutable beta.2 tag on commit `ca640d652a5b72bc67f3cb169b4a319862c853f5`. They are **not installed automatically** on existing Home Assistant instances: users need a verified HACS update and integration restart. They do **not** certify Deye physical startup behavior, independent AC output, safe-off under load, 24-hour operation, or a tested HACS rollback.

### Added

- Automatically suggest the only complete Forecast.Solar configuration without guessing PV-to-storage allocation when multiple independent sources exist ([#11](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/11), PR #141).
- Detect BK215/B215 Plus nominal capacity from `2,150 Wh × positively verified physical modules`, and suggest supported Deye-rated inverter watts from provider-owned device evidence. Preserve older manual mappings and the exclusive export mode ([#140](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/140), PR #144).

### Changed

- Coordinate two eligible storage heads earlier under sustained independently measured grid import; derive a bounded adaptive import buffer from recent signed-meter behavior. Inverter first-start steps remain conservative pending hardware proof (PR #143).
- For recognized BK modules, treat the **actual BMS hardware charge ceiling** as full (for example 90% even if a configurable charge target is 100%), and do not use redundant manual upper-SoC values for this decision. PV-only export requires fresh, credible **tower-attributed PV input watts** and drops back to normal household matching when evidence is lost (PR #142).
- Retain previously selected PID regulation on upgrades rather than silently changing a live controller. Offer an explicit, one-time Expert migration to the unified regulator; hide the competing algorithm switch while retaining legacy settings for rollback (PR #158).

### Fixed

- Preserve the normal signed-grid startup/export protections when a user selects PV mode but BMS full and measured PV availability have not been established (PR #142).
- Do not silently reduce a tower to 2,150 Wh nominal when registered B215 expansion telemetry becomes entirely unavailable or stale (PR #149).
- Clear resolved inverter write/transport error status only when all required safe-stop acknowledgements have independently completed (PR #150).
- Never stack an additional positive actuator write while its preceding command still lacks independent hardware acknowledgement; allow emergency reductions (PR #151).
- Detect lost or corrupt persisted shutdown journals after initial setup, rearm minimum-output obligations and keep errors visible until independent confirmation (PR #155).
- Reject the first positive Solarman command for an unverified control/readback profile, while retaining emergency minimum attempts without false acknowledgement (PR #156).
- Keep the controller deadband relative to the chosen/effective grid import target, and exclude isolated export/load outliers from the adaptive import-reserve estimate (PR #157).

**Field-validation outstanding:** Physical Deye 0→positive AC output spikes (#53), genuine safe-off on every inverter including reconnection (#32/#49), meaningful 24-hour two-device closed-loop operation, actual HACS installation/update/rollback (#8), and the release gate (#140). Full controlled economic forecasting and dynamic tariffs are **not stable 1.0.0 functionality**.

## [1.0.0-beta.1] - 2026-11-16

**Experimental prerelease; actual device AC behaviour and unattended hardware operation remain unvalidated.** This section consolidates the important features and fixes from the legacy `0.1.0b3–0.1.1b18` experimental releases and subsequent development. **Most features listed below already exist in published betas**; their inclusion here is the curated baseline for the 1.0.0 beta, not a claim of new implementation or stable physical acceptance.

### Added

- Guided Home Assistant setup for SunEnergyXT BK head units, configurable inverter power-limit entities and a signed whole-site grid meter; automatic discovery supports the original and renamed `sunenergyxt_bk` integration domains, with manual corrections and reconfiguration.
- One coordinated controller across independent storage towers, each with its own lower SoC reserve, upper limit, inverter assignments and optional EV3600 charging guard; capacity-weighted overall SoC uses configured Wh including extension modules and shows unknown when data is incomplete.
- User-selectable household-demand regulation or PV-after-full-charge feed-in (off by default), with upper-SoC hysteresis and optional measured PV-input limiting. The PV mode may feed stored energy into the grid.
- Optional adaptive PID (bounded Kp/Ki/Kd), startup settling hold and Home Assistant switches/number controls. Live diagnostics distinguish signed grid power, PID terms, target/requested limit, HA-reported limit and independently measured inverter AC power when configured and fresh.
- Shutdown/reconnection obligations and command acknowledgement tracking, including direct Deye/Solarman register-53 readback **only for the explicitly checked profile**. Safe-minimum requests and readback errors are not presented as proof of measured zero AC output.
- Read-only per-inverter availability reasons and separate measurement-source diagnostics. Operating-history evidence and passive demand/Forecast.Solar collection retain timestamp, freshness, receipt and persistence uncertainty rather than inventing observations.
- A vendor-neutral [arc42 architecture](docs/architecture/README.md), [ADRs](docs/adr/), [compatibility matrix](docs/device-compatibility.md) and documented release/change policy.

### Changed

- Power-increase dispatch includes confirmed-import-aware bounded shared budgets and limits per command; first start remains conservative and pending acknowledgements prevent unverified repeated increases. The software limit is **not** a tested physical inverter ramp limit.
- Each storage head now has an optional, validated combined AC setpoint budget in Configure; old entries retain their conservative 1,920 W default without confusing inverter AC setpoints with observed DC output.
- Setup permits **up to four distinct inverter power-limit mappings per storage head**, rather than two; whether an inverter can be controlled safely depends on its vendor/firmware feedback contract and the head's actual physical limits.
- Home Assistant / HACS distribution and upgrade documentation no longer assume one household's Proxmox, Shelly or Deye configuration.

### Fixed

- PID integral carry-over after entering the grid-target deadband or reversing import/export direction, which could otherwise command an inappropriate increase.
- A severe startup net-export sample no longer causes an irreversible software lockout: subsequent distinct meter reports trigger proportional reductions and a staged restart only after settled actuator feedback and fresh stable import (PR #129).
- A normal change of preferred storage head no longer forces the previous, still-eligible inverter to 0%; allocation remains bounded by its actual available reserve, confirmed limits and independent safety gates.
- Recovery of pending inverter minimum commands across disconnect/reconnect, startup and Home Assistant restart; errors remain explicit if the hardware cannot be reached or acknowledged.
- Loss or staleness of one configured storage DC sensor now lowers only its own inverter outputs and keeps healthy independent heads eligible after acknowledged safe reduction; diagnostics identify the affected tower.
- Device and source identity checks for the supported Deye AC-voltage guard, including owner-config-entry verification after registry changes. Missing or stale voltage inhibits new positive power requests for that profile.
- Removed the unapproved automatic permanent software shutdown on a BK DC telemetry sample above 1,920 W. Manufacturer/BMS protection remains external; old stored DC-trip options no longer lock the controller.
- Reconfiguration preservation of existing SoC/limit mappings, clearer optional PV power-source validation/removal, and forecast source binding/receipt handling across late loads and restarts.
- Newly configured EMS instances now start disabled and require explicit activation; existing enabled/disabled intent is preserved through upgrades.

### Removed

- Twenty-seven duplicated `RELEASE_NOTES_*.md` files from the repository. Their important user-facing history is consolidated here; previously published [GitHub Releases](https://github.com/schneider-de-com/ha-sunenergyxt-ems/releases), tags and Git history remain intact. Future prerelease notes are generated from this changelog.

**Field-validation gaps:** The original Deye M200 95.5%→0% transition and large net export on 15 November are not yet proven to a physical root cause. The controller-side permanent software lockout, the unnecessary emergency role-switch zero, the unsupported DC software latch and the all-tower stale-DC reaction have been corrected and regression-tested; **this does not demonstrate that every physical Deye 0%→positive AC transient or every inverter firmware is safe**. The newly introduced BK215 Plus and non-Deye inverter mappings still require separately verified model/firmware write/readback and actual AC behavior. This is **experimental beta software, not approved for unattended operation**. See [#53](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/53), [#27](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/27) and [the compatibility matrix](docs/device-compatibility.md). **Stable `1.0.0` remains gated by meaningful 24-hour two-inverter field evidence, real device acknowledgements and tested install/update/rollback.**

Forecast-based economic optimization and automatic tariff dispatch are **outside the 1.0.0-beta.1 core scope** and require separate assessment.

## Historical prereleases

The existing published `0.1.x` betas use the former Python-style suffix (for example `0.1.1b18`), which is **not valid SemVer prerelease syntax**. Historical tags are not renamed or rewritten. The published SemVer prerelease is [`v1.0.0-beta.1`](https://github.com/schneider-de-com/ha-sunenergyxt-ems/releases/tag/v1.0.0-beta.1); stable `v1.0.0` has **not** been published.

Original release records, their exact commit references and downloadable packages remain in [GitHub Releases](https://github.com/schneider-de-com/ha-sunenergyxt-ems/releases).

[Unreleased]: https://github.com/schneider-de-com/ha-sunenergyxt-ems/compare/v1.0.0-beta.1...main
[1.0.0-beta.1]: https://github.com/schneider-de-com/ha-sunenergyxt-ems/compare/v0.1.1b18...v1.0.0-beta.1
