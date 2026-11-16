# ADR-0006: One adaptive proportional regulation policy before stable 1.0.0

- Status: **Accepted** (implementation in `1.0.0-beta.3`; field validation open).
- Date: 2026-11-16
- Related: #145, #27, #68, #53, #32, #140
- Supersedes: the competing PID-versus-proportional **strategy selection** only; does **not** supersede ADR-0001's single-writer/independent hardware feedback rule.

## Context

Early experimental betas exposed two grid-feedback algorithms and several PID gain/
integral/derivative options. This made behavior depend on an invisible legacy
configuration and forced unnecessary migration UI. The project has not released
a public stable version and has no other installed users according to its owner.
The owner explicitly authorized removing this experimental compatibility layer.

## Decision

Use exactly one pure `Controller.update` feedback algorithm:

1. Read a fresh signed *whole-site* grid value and the configured/effective
   import target. Positive values are import; negative values are export.
2. Treat a small target-relative, non-exporting deadband as settled. Otherwise,
   request a bounded correction from the independently confirmed inverter
   outputs: proportionally lower on export, cautiously increase on import.
3. Retain the existing adaptive signed-meter noise filter, paired-sample
   reserve, coordinated tower allocation, per-tower SoC/BMS rules,
   startup rate/settling limit, verified inverter readbacks, and safe
   shutdown obligations in the unchanged runtime boundaries.

Remove the alternate PID branch, integral/derivative state and tuning entities.
No user algorithm selector or special pre-stable migration flow exists. Old
experimental PID flags are ignored by the runtime and discarded when options
are saved; valid hardware mappings, enable intent, reserve and feed-in options
are preserved. Diagnostics report a single `adaptive_proportional` mode and
a signed error/correction, not fictional PID terms.

## Consequences

There is one behavior to test and troubleshoot, with fewer HA entities and
no PID integrator that might fight export recovery. Earlier beta PID curves
are *not* preserved; HACS rollback to `1.0.0-beta.2` remains possible if
the new physical behavior is worse. This is an intentional pre-stable change,
not proof of safe operation. Verify the actual Deye M200/M80 firmware and
0→positive AC response, loaded manual OFF/reconnect and at least 24 hours
of meaningful two-head closed-loop operation before promoting to stable v1.0.0.
