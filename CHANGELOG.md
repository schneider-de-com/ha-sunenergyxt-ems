# Changelog

Notable user-facing changes are documented here, by release, following
[Keep a Changelog 1.1.0](https://keepachangelog.com/en/1.1.0/) and
[Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).

This is a curated human-readable changelog, not a dump of Git commit subjects.

## [Unreleased]

### Added
- Release policy covering SemVer, Conventional Commits, stable hardware acceptance and rollback.
- Compact arc42 architecture reference and Architecture Decision Records (ADRs).
- Device-neutral arc42 and ADR-0004 for BK215/BK215 Plus storage towers and vendor-specific inverter capabilities.
- Compatibility matrix separating SunEnergyXT-approved electrical pairings from proven Home Assistant EMS hardware control.

### Changed
- Initial setup accepts up to four distinct inverter power-limit entities per storage tower instead of two; this does not claim untested inverter hardware support.

## Version history

The existing experimental version **0.1.1b18** uses a legacy Python-style
prerelease number, not a valid SemVer prerelease identifier. It is **not**
retroactively renamed or presented as a stable release. Future tags will use
SemVer-compatible names, for example 1.0.0-rc.1 and 1.0.0.

**Version 1.0.0 has not been released.** It must not be added as a dated
version entry until the two-inverter live acceptance, safety/ACK checks,
supported installation/update and rollback have actually passed.

The existing published beta history remains available in
[GitHub Releases](https://github.com/schneider-de-com/ha-sunenergyxt-ems/releases).

[Unreleased]: https://github.com/schneider-de-com/ha-sunenergyxt-ems/compare/main...HEAD
