# Contributing

## MVP scope

The six MVP issues describe user-facing features. Forecasting, tariffs, and seasonal automation are out of scope.

## Definition of Done

Every MVP feature must:

- Deliver its issue's user-facing acceptance criteria.
- Use test-driven development: failing test, minimal implementation, refactor.
- Pass unit and Home Assistant integration/config-flow tests, linting, and strict type checks.
- Follow Home Assistant's current Integration Quality Scale rules where applicable; document any justified exemption. A custom integration is **not** officially graded Platinum by Home Assistant.
- Keep domain logic separate from Home Assistant and device APIs. Prefer simple, maintainable code.
- Respect inverter and BK215 limits, missing data, restarts, and safe manual shutdown.
- Avoid extra device protocols and acknowledge that Home Assistant service success does not prove hardware response.
- Provide clear user-facing labels and translations, setup and troubleshooting docs.
- Pass CI. Obtain real hardware evidence before claiming a device/firmware combination is hardware-validated.

## Behavioral acceptance gate

Every user-facing acceptance criterion needs a test that can fail if the user
behavior is missing. Code coverage and mocked method invocations alone are
not evidence of a working Home Assistant integration.

- For installation and config-flow changes, test the supported HA version's
  setup/entry lifecycle, correct integration category, device/entity registration,
  options and translations. Finish with an actual UI smoke check of
  **Settings → Devices & Services → Integrations** before calling it validated.
- For inverter control, test *sequences*, not only isolated updates: stale
  sensor data, delayed acknowledgements, out-of-order values, changing
  household load, grid sign changes, simultaneous battery systems, partial
  failures and state after restarts.
- Model the real contract: a successful `number.set_value` service call is
  not inverter acknowledgement; a new meter sample must represent a time
  after the last command and its settling period. Emergency safety limits
  must remain effective while normal control is gated.
- Store deterministic replay fixtures for any production incident before
  altering controller behavior. Compare command count, overshoot, recovery
  and reserve behavior with the pre-fix baseline.
- Demonstrate **RED → GREEN → REFACTOR** in the PR: include the initial
  failing test output and the final passing result. Do not label a bug
  resolved merely because an unrelated unit suite or static validator is green.
- Treat all required lint, format, type, security, HA compatibility and test
  steps as blocking, not `continue-on-error`. If the existing repository
  still violates a gate, call it out and fix it before claiming full compliance.
- Do not release a build for unattended hardware control based solely on CI.
  Document hardware validation and a safe rollback/update path separately.

Do not merge incomplete or untested control code into a production-ready release.

## Commit messages

Use a short Conventional Commit message that names the related user story:

```
feat(config): suggest BK215 devices (Refs #1)
test(control): cover invalid grid measurements (Refs #4)
```

Every project commit must reference its related GitHub issue with `Refs #<number>`.
Use `Fixes #<number>` only when the issue is fully complete.
Keep infrastructure and test work inside the existing MVP feature issues; do not open separate engineering-only tickets.
