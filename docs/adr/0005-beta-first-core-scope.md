# ADR-0005 — Deliver the core scope as a 1.0.0 beta before stable acceptance

- **Status:** Accepted; supersedes the release-stage assignment in [ADR-0002](0002-core-release-scope.md), not the safety evidence requirements.
- **Date:** 2026-11-16
- **Related:** #8, #27, #37, #49, #53, #68

## Context

ADR-0002 originally assigned the complete tested core to stable 1.0.0.
However the project is still experimental: certain Deye startup/feedback
faults, multi-device control behaviour, vendor-specific compatibility and
live safety/rollback acceptance are unresolved. Holding all core features
behind a stable label makes it difficult for reviewers to test a coherent
candidate, while calling a beta stable would misrepresent its maturity.
The project has also accumulated dozens of standalone prerelease-note files.

The user requested that the originally intended **1.0.0 functional scope
instead become a beta milestone**, retaining the later stable release as
a verified promotion rather than expanding the target feature set.

## Decision

1. The full core set originally planned in ADR-0002 becomes the **target
   scope of `1.0.0-beta.1`**, including multi-tower/battery reserve control,
   signed-grid feedback, model-specific confirmed inverter writes, recoverable
   export correction, safe OFF/restart, deliberate PV-export policy and native
   diagnostics. This is a scope target, **not** a statement that all defects
   are fixed or all hardware already validated.
2. Use **SemVer** prerelease tags (`v1.0.0-beta.1`, then later
   `v1.0.0-beta.2` / `v1.0.0-rc.1` as appropriate); keep historical
   `v0.1.xbN` tags unchanged. Release candidates are not stable versions.
3. A beta requires green automated checks, coherent installation/upgrade
   packaging, testable bounded control and explicit disclosure of remaining
   hardware and model/firmware gaps. **Unattended live hardware control is not
   approved merely by publishing a beta.** Never hide an unacknowledged stop.
4. Publish stable **`v1.0.0` only after** adequate real two-inverter
   operation, acknowledged stop/recovery and actual tested rollback for the
   declared supported configurations. New vendors/Plus arrangements require
   separate actual-device acceptance before a “supported” claim.
5. Maintain **one curated `CHANGELOG.md`** using Keep a Changelog and
   generate prerelease text from its Unreleased section. Old individual note
   files can be removed from the active tree after consolidation; historical
   published releases, tags, and git history are retained.
6. Economic forecast optimisation (including its independent seven-day
   validation) and dynamic-tariff dispatch are not in beta 1.0.0 core scope.

## Alternatives considered

- **Hold the original 1.0.0 feature target for stable publication:** blurs
  feature completeness with physical acceptance, delaying useful betas.
- **Publish stable 1.0.0 as soon as software tests pass:** overstates
  hardware readiness and rollback safety; rejected.
- **Beta-first scope with evidence-based stable promotion (chosen):** allows
  an understandable beta without relaxing honest validation requirements.

## Consequences

The release docs, README and changelog must distinguish **features targeted
for 1.0.0-beta.1** from **physical evidence required for stable 1.0.0**.
An open issue or draft PR does not become completed merely because it appears
in the beta milestone. Existing hardware and UI configurations stay intact;
this ADR alone does not change a live controller or create a release tag.

Follow [docs/releasing.md](../releasing.md) and the
[compatibility matrix](../device-compatibility.md) for acceptance evidence.
