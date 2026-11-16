# Release and commit policy

These rules apply to new work; never rewrite or retag the historical beta releases.

## Semantic Versioning

Use [SemVer 2.0.0](https://semver.org/spec/v2.0.0.html) for versions and
immutable tags. Tags carry a **v** prefix; the integration manifest contains
the bare SemVer version.

- **MAJOR**: incompatible public integration API, entity identifiers or options
  migration that cannot preserve existing working configuration.
- **MINOR**: backwards-compatible features, options, devices or sensors.
- **PATCH**: backwards-compatible bug fixes and their tests.
- **Prerelease**: 1.0.0-beta.1, 1.0.0-beta.2, then 1.0.0-rc.1 when
  appropriate. A beta is experimental and **not** a stable 1.0.0.

A maintenance-only docs or CI change does not require a runtime version bump.
Mark releases with a tested commit SHA and the known supported combinations.
A stable tag requires all release gates; CI alone is insufficient.

## Conventional Commits

Use [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/)
with an imperative subject and a reference to the existing focused issue.

- feat(control): coordinate independent inverter output (Refs #68)
- fix(control): recover from verified net-export transient (Refs #53)
- test(hardware): replay startup export evidence (Refs #53)
- docs(architecture): record the single-writer decision (Refs #8)
- ci(release): verify commit message conventions (Refs #8)

Allowed types: feat, fix, docs, test, refactor, perf, build, ci, chore, style, revert.
Breaking changes use an exclamation mark after the type/scope or a
BREAKING CHANGE: footer, with migration notes and a MAJOR bump after v1.0.0.
A feat normally implies MINOR and a fix normally implies PATCH **after 1.0.0**,
but a commit does not automatically publish a new release. Reverts and
multiple commits are evaluated by their *actual* user-visible impact.

Use **Refs #N** for ongoing work, **Fixes #N** only when all issue acceptance
criteria are verified. Prefer one focused PR and squash it using the checked
Conventional Commit PR title. PRs and their commits should explain *why* the
change matters, not merely state a filename.

## Keep a Changelog

Maintain CHANGELOG.md manually for meaningful changes, grouped by:
Added, Changed, Deprecated, Removed, Fixed and Security. Maintain an
[Unreleased] section; move validated entries into a dated version section
**only on the actual release**. Avoid creating empty categories or reprinting
commit messages. Link material issues/ADRs and state hardware or firmware
restrictions plainly.

## Beta scope: 1.0.0-beta.1

**ADR-0005 supersedes the original 1.0.0 scope allocation in ADR-0002.**
The full core feature scope is targeted at **1.0.0-beta.1**, not promised
as already field-accepted stable 1.0.0. Previously published 0.1.x beta
tags remain immutable; no automatic retagging or forced live activation.

The beta target covers:

1. Signed whole-site grid following with independently eligible BK215 and
   BK215 Plus heads; extension capacity is configured per tower, and every
   lower SoC reserve is enforced independently.
2. Coordinated, bounded multi-inverter allocation, recoverable net-export
   correction, true model-specific hardware limit ACK when the integration
   claims support, safe manual OFF/HA restart, and isolation of failures.
   Close the known startup/zero-percent and settling defects (#27, #53, #68).
3. User-selected no-intentional-export or PV-after-full-charge modes, simple
   HA-native configuration, useful status/diagnostics and retained settings.
4. Device-neutral arc42, ADRs and a clear, **evidence-based** compatibility
   matrix, with non-Deye/Plus hardware explicitly marked as unvalidated
   until its own firmware/actuator tests pass.
5. Reproducible positive and negative tests, HA configuration/options and
   lifecycle suites, style/type/security checks, HACS/Marketplace packaging,
   and documented installation/rollback steps.

**Beta publication rule:** all mandatory software/CI and packaging gates
must pass. A beta can disclose explicitly unresolved *field* acceptance and
must be labelled **experimental / not approved for unattended operation**.
Never publish a known irreversible control lockout as a supported use case,
silently expand vendor support, or suppress a failed shutdown ACK. Keep the
manifest version, tag and release notes generated from the curated
`CHANGELOG.md` consistent.

## Stable 1.0.0: evidence-based promotion, not another feature bucket

Only promote a beta-derived, **unchanged functionally scoped** candidate
once these independent acceptance criteria pass:

1. At least **24 hours of meaningful two-inverter closed-loop field
   operation** for the initial declared reference combination, covering
   import, export/load drop, startup, settling, actual power feedback,
   offline/recovery and confirmed shutdown. Inactivity is not a pass.
2. Specific ACK and physical output/ramp/zero-percent behavior for **each
   newly claimed** head/inverter/firmware/provider combination; a Deye
   reference test never automatically certifies Hoymiles, APsystems, TSUN
   or BK215 Plus electrical limits.
3. Confirm supported HACS/Marketplace clean install, upgrades with option
   preservation and a **real tested rollback** to the last verified build.
4. Record exact commit, HA version, inverter firmware, observation
   timestamps, gaps and failures; publish immutable `v1.0.0` and dated
   changelog entry only with truthful pass/fail evidence.

**Economic forecast planning** remains experimental and will need its
separate seven-day evaluation before an optional later 1.x feature
claim. Dynamic tariff hardware dispatch is outside the 1.0.0-beta.1
core. Do not equate a green CI badge with physical safety.
