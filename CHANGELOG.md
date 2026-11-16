# Changelog

Human-readable changes follow [Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html). Version tags are immutable.

## [Unreleased]

**Target milestone: `1.0.0-beta.1` (not yet published).** This section consolidates the important features and fixes from the legacy `0.1.0b3–0.1.1b18` experimental releases and subsequent development. **Most features listed below already exist in published betas**; their inclusion here is the curated baseline for the 1.0.0 beta, not a claim of new implementation or stable physical acceptance.

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
- A normal change of preferred storage head no longer forces the previous, still-eligible inverter to 0%; allocation remains bounded by its actual available reserve, confirmed limits and independent safety gates.
- Recovery of pending inverter minimum commands across disconnect/reconnect, startup and Home Assistant restart; errors remain explicit if the hardware cannot be reached or acknowledged.
- Loss or staleness of one configured storage DC sensor now lowers only its own inverter outputs and keeps healthy independent heads eligible after acknowledged safe reduction; diagnostics identify the affected tower.
- Device and source identity checks for the supported Deye AC-voltage guard, including owner-config-entry verification after registry changes. Missing or stale voltage inhibits new positive power requests for that profile.
- Removed the unapproved automatic permanent software shutdown on a BK DC telemetry sample above 1,920 W. Manufacturer/BMS protection remains external; old stored DC-trip options no longer lock the controller.
- Reconfiguration preservation of existing SoC/limit mappings, clearer optional PV power-source validation/removal, and forecast source binding/receipt handling across late loads and restarts.
- Newly configured EMS instances now start disabled and require explicit activation; existing enabled/disabled intent is preserved through upgrades.

### Removed

- Twenty-seven duplicated `RELEASE_NOTES_*.md` files from the repository. Their important user-facing history is consolidated here; previously published [GitHub Releases](https://github.com/schneider-de-com/ha-sunenergyxt-ems/releases), tags and Git history remain intact. Future prerelease notes are generated from this changelog.

**Still open for `1.0.0-beta.1`:** correct the permanent export-only startup lockout ([#53](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/53), draft [PR #129](https://github.com/schneider-de-com/ha-sunenergyxt-ems/pull/129)); confirm stable coordinated inverter dispatch and independent partial-failure recovery ([#27](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/27), [#68](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/68)); and document tested actuator profiles honestly. Beta publication requires software/installation checks and known limits, not a claim of safe unattended field operation. **Stable `1.0.0` remains gated by meaningful 24-hour two-inverter field evidence, genuine hardware ACK and tested update/rollback.**

Forecast-based economic optimization and automatic tariff dispatch are **outside the 1.0.0-beta.1 core scope** and require separate assessment.

## Historical prereleases

The existing published `0.1.x` betas use the former Python-style suffix (for example `0.1.1b18`), which is **not valid SemVer prerelease syntax**. Historical tags are not renamed or rewritten. The next beta will use `v1.0.0-beta.1`; stable `v1.0.0` has **not** been published.

Original release records, their exact commit references and downloadable packages remain in [GitHub Releases](https://github.com/schneider-de-com/ha-sunenergyxt-ems/releases).

[Unreleased]: https://github.com/schneider-de-com/ha-sunenergyxt-ems/compare/v0.1.1b18...main
