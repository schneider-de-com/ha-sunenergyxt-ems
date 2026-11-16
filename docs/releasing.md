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
- **Prerelease**: 1.0.0-rc.1, 1.0.0-rc.2, and so on, until the real acceptance
  gate passes. A release candidate is **not** a stable 1.0.0.

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

## Release 1.0.0: required gates

1. Confirm the v1.0.0 **core-only scope** in ADR-0002. Do not silently
   release experimental economic forecast planning or dynamic tariff control.
2. Finish the open core control defects, including Deye startup/zero-percent
   transients, regulation settling, actual register readback and correct
   mode-aware PV export. Preserve manual-off safety, reservations and
   user-selected options. Do not use a sensor-only >1920 W DC software latch.
3. Run deterministic negative/positive regressions, native HA config/options
   and lifecycle tests, type/format/security checks, and all required CI
   jobs. Document initial failing reproduction and final verified behavior.
4. Observe at least **24 hours of meaningful live two-inverter control**
   in the initial reference combination, including actual power feedback,
   fresh meter observations, offline/recovery and shutdown behavior.
   This is a release reference test, **not** a Deye-only product requirement.
   Do not claim support for any additional head/inverter/firmware combination
   before its own compatibility and physical ACK/settling validation.
   Report gaps and inactivity; elapsed time alone does not count as success.
5. Verify supported HACS/Marketplace clean install, upgrade, configuration
   preservation and a real rollback to the last verified build. A manual
   documented rollback is acceptable when it is tested; do not claim an
   automatic rollback facility.
6. Record the exact commit, Home Assistant and inverter firmware versions,
   observation window, errors and explicit pass/fail/insufficient-evidence.
   Publish the tag, manifest version and short release notes only after
   all applicable checks pass.

**Economic forecast planning** is deferred to a later 1.x release with
its own seven-day field assessment. The future price strategy requires
separate evidence and explicit user activation.

Do not make an unvalidated controller available as an unattended stable
release. Document remaining blockers rather than creating an empty v1.0.0 tag.
