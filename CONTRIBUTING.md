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

Do not merge incomplete or untested control code into a production-ready release.
