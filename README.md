# SunEnergyXT EMS for Home Assistant

> **Status: Planning / pre-alpha — not yet installable.**
>
> This is an independent community project and is not affiliated with or endorsed by SunEnergyXT or the Home Assistant project.

**SunEnergyXT EMS** is a planned Home Assistant custom integration for coordinating SunEnergyXT BK215 battery storage systems and controllable microinverters. Its primary goal is to reduce electricity costs through efficient solar self-consumption, while avoiding unnecessary grid feed-in and battery cycling.

## Planned first release (MVP)

- **Home Assistant configuration flow** with suggested entity selections from the existing Energy Dashboard where feasible, plus manual correction and selection.
- **Multiple BK215 systems**, with **one or two individually controllable microinverters per BK215**; the two inverters may use different manufacturers or integrations.
- **Vendor-independent inverter control through Home Assistant entities**, supporting power limits expressed in **watts or percent** and respecting device-specific output limits.
- **Load-following power regulation** using grid measurements, battery state of charge (SoC), and suitable protection logic.
- **Optional feed-in after the battery reaches its configured full-charge threshold**.
- **Capability-aware inverter handling**, including device-dependent command intervals, availability and feedback, rather than assuming all integrations behave alike.

The planned integration will use existing Home Assistant integrations and entities wherever possible rather than implement separate communication protocols for each inverter manufacturer.

## Longer-term goals

After the first stable release, potential enhancements include:

- Self-learning household consumption forecasts that give more weight to recent usage.
- Solar production and electricity price forecasts.
- Cost-aware energy scheduling for both fixed-price and dynamic electricity tariffs.
- More sophisticated seasonal storage strategies and grid-friendly operation.

These are **roadmap goals**, not features that are currently implemented.

## Development status

The repository is being initialized. **There is no functional integration, installation package, or released version yet.** Please do not treat the repository as ready for use in a production energy system.

During development, device safety and reliable behavior take precedence over aggressive regulation. Inverter control will only be considered suitable after its supported capabilities and failure modes have been verified. The integration must not operate in parallel with another controller writing to the same inverter power-limit entity.

## Contributing

Ideas, compatibility reports, and technical findings are welcome through GitHub Issues. Implementation instructions, development conventions, and a contribution guide will be added as the project progresses.

## License

Licensed under the [Apache License, Version 2.0](LICENSE).
