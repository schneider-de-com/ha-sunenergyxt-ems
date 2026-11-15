# Changelog

All notable changes to SunEnergyXT EMS are recorded here for users.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Starting with the first SemVer prerelease, versions follow
[Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html).
Earlier beta tags ending in `bN` are historical Python-style tags and are
not retroactively renamed.

## [Unreleased]

### Added

- Human-readable changelog and a documented SemVer / Conventional Commits
  release policy for future releases.
- Explicit stable-release acceptance gate: automated checks, real hardware
  evidence, configuration migration, and verified rollback.

## [0.1.1b18] - 2026-11-15

### Changed

- Positive Deye inverter limit increases now share a proportional,
  confirmed-import ramp budget across both devices.
- Existing readback, startup-hold, shutdown and HA diagnostics remain in place.

### Known limitations

- Experimental beta, not a hardware-validated stable release.
- A startup export event can leave b18 in a persistent error state; see
  the live 2026-11-15 incident and the dedicated recovery PR.
- Real two-inverter 24-hour acceptance, physical startup-export behavior,
  and safe release rollback are not yet verified for v1.0.0.
- Economic PV / demand forecast planning is reserved for a later v1.x release.

See [beta notes](RELEASE_NOTES_0.1.1b18.md) for the detailed historical
software changes and validation boundaries.
