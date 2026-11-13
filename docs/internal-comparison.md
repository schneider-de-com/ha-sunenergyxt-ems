# Internal strategy comparison

`comparison.compare` calls the pure priority planner once for both baseline and
planned replays using the exact same load, per-storage PV, capacities and
controller snapshot. It returns modeled import Wh, modeled DC overflow,
modeled battery discharge and remaining usable DC energy. It does not fetch
measurements, change configuration or issue commands. No product dashboard or
active dynamic tariff mode consumes this module.

The same replay is priced in two independent scenarios: an explicitly supplied
flat EUR/kWh price, and optional verified Green Planet Energy `get_prices`
quarter-hour intervals (`start`, `end`, `price`). The caller verifies provenance,
currency and units; the parser cannot authenticate a JSON file. Negative prices
are supported. Prices must cover the full replay horizon without gaps or
overlap, including zero-import periods. Missing tariffs stay unknown; missing
intervals fail rather than receiving a zero/default price. Offset-aware absolute
timestamps avoid DST ambiguity. Costs exclude fixed fees and export revenue.

Import is assumed uniform within each model load slot when it overlaps
quarter-hour price boundaries. This is a model-resolution assumption, not a
claim of quarter-hour metering. Dynamic prices never enter the planner: this
simulates how the flat-price strategy's energy flows would be billed under the
real GPE intervals, not a hypothetical optimal dynamic-tariff controller.

Period import bills alone do not value final stored energy. Use the separately
reported terminal-energy-adjusted planning advantage when judging whether
reduced period import is more than shifting consumption in time. Model overflow
is potential DC energy beyond modeled storage headroom, not measured curtailment.
Export and measured curtailment are `None`, not fabricated zero measurements.
The separate real-operation ledger and seven-day acceptance remain necessary.

Private household traces should stay in their authorized environment. Public
tests use synthetic energy traces; external GPE price intervals can be fetched
read-only for internal weather/load scenario replay. Neither simulation results
nor local files alone establish durable actual-operation evidence.
