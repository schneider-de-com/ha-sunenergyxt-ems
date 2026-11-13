# Forecast storage priority

Configure enables optional forecast storage priority. It is off by default.
Existing operating settings and measurements remain in place. The planning
sensor explains why a hint is available or why ordinary household regulation
continues; it is distinct from the hardware/control phase.

The horizon covers the remaining local day and the following night up to the
next local date's astronomical sunrise. Complete recent household history,
confirmed history storage, usable capacities and valid Solcast estimates are
required. A single complete Solcast entry can be suggested automatically;
the three source fields remain editable. Independent battery forecasts can
be mapped explicitly. With multiple batteries, an aggregate positive solar
forecast cannot be assigned arbitrarily: the planner reports missing allocation.
Missing data never becomes zero. Duplicate per-battery forecasts are rejected.

The planner compares candidate main/support ordering with the existing policy
on identical demand and solar intervals. It can prioritize the storage expected
to receive later solar, freeing headroom before it fills. It changes only the
order of eligible storage, not household demand, grid target, reserves or power
budget. It has no grid-charging command and never adds a fixed evening reserve.
Hints expire after 90 seconds and are cleared when data or options invalidate
them. Reserve/DC/readback/shutdown/reconnect gates and the existing writer remain
authoritative. An option to release maximum output on full charge takes priority;
future full-charge export with positive forecast solar is outside this model.

The energy replay is an idealized half-hour model. It uses measured initial SoC,
configured capacity/limits and forecast energy, not an inverter transient model.
It tests conversion efficiencies of 80%, 90% and 100% and combinations of the
demand bounds with each storage's solar bounds. Residual usable energy is
credited consistently to both strategies:
emptying more battery by the horizon end is not counted as a flat-price saving.
A two-percentage-point capacity allowance rejects small modeled differences;
this is an engineering decision margin, not a user acceptance threshold or
claimed calibrated SoC accuracy. Lower/upper forecasts are tested scenarios,
not guarantees about every future weather/load path. Actual improvement needs
operating evidence; simulated overflow is not measured PV curtailment.

Replay runs on an executor worker, not in the hardware-control event loop.
Each result uses detached inputs and is rejected after source, storage or option
changes, including an individual today/tomorrow/API source version change.
Results taking more than 15 seconds are discarded, even if observations have
continued to arrive. Storage eligibility is rechecked even while a cached hint exists.
Entry unload cancels delivery of an outstanding result; the pure worker cannot
issue a command. Normal household regulation continues during evaluation.

Model comparisons and expected advantage are retained in diagnostics for internal
inspection. The product sensor shows the operational reason and proposed order,
not a comparison dashboard. Dynamic-tariff simulation and the actual measured
strategy comparison are separate Sprint-2 work; there is no active dynamic
tariff control here. Green CI does not establish hardware acceptance or start
the required seven-day installed-feature observation.
