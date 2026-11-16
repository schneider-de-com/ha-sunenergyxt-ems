# ADR-0002: Release verified two-inverter core before economic forecasting

- Status: **Superseded in milestone allocation by [ADR-0005](0005-beta-first-core-scope.md)**; retained as the 2026-11-15 historical decision
- Date: 2026-11-15
- Related: #8, #37, #53, #68, #92

## Context

A stable 1.0.0 should be understandable and reversible. The real
M200/M80 startup behavior, register readback and consistent load-following
still have open hardware/field acceptance. Forecast planning in b18 is
experimental: its demand and PV measurements are incomplete and it does
not have a successful seven-day economic field assessment.

## Decision

The first stable 1.0.0 contains the tested **core** load-following
controller for both inverters, per-stack reserves, real ACK and safe
disable/restart, optional user-selected PV-after-full versus no-export,
simple configuration, status and verified installation/update/rollback.

Release only after targeted regression/HA CI checks and **at least
24 hours of meaningful two-inverter measured operation**, with failures,
gaps and limitations recorded. Keep economic forecast planning outside
the 1.0 stable feature claim. Implement and validate it separately in
a later 1.x release after at least seven consecutive measured days.
Active dynamic-price hardware dispatch is a still-later optional proposal.

## Consequences

The stable core can be delivered without misrepresenting experimental
forecasts as validated savings. Existing beta planning code may remain
a clearly marked experimental capability, but cannot silently control
the stable core or be advertised as production-tested.

This ADR does not waive any hardware/ACK check or authorize early release.
Use SemVer, Conventional Commits, curated Keep a Changelog notes and an
actually verified rollback. See docs/releasing.md.
