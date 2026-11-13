# SunEnergyXT EMS v0.1.1b10 — experimental bounded import response

Includes Forecast.Solar source support from b9 and the existing PR #73 candidate.
After three distinct, ordered grid samples above 500 W, its existing bounded
20 W upward steps may use the actuator's minimum interval. First tiny starts,
startup hold, device acknowledgements, settled/new meter feedback, reserve/DC
limits, shutdown and reconnect remain authoritative.

The faster path remains **disabled when feed-in is enabled**. The current
installation has feed-in enabled; retain that preference and do not promise
faster real convergence from this candidate there. No setting is changed to
force a favorable demonstration. Missing/backward meter samples still allow
unacknowledged commands to time out, including multiple outstanding actuators.

Measured incident replays and ideal closed-loop simulations are test evidence,
not a measured counterfactual or hardware-transfer-function certification. The
known firmware/register startup transient remains under #53. The 500 W sample
threshold is a controller policy, not a user-accepted export/trip limit.

Experimental installation: preserve options, current intent and journal
checkpoint; safe-off through the existing switch and independently confirm
actuator minima. Install this exact prerelease and restart once. Read back the
loaded version, unchanged options/obligations and retained journal/gaps before
restoring the previous enable intent after existing safety checks. Select the
user-requested Forecast.Solar today/tomorrow pair through the supported options
flow and verify the stored mapping independently. Do not force provider polling
to manufacture a fresh receipt; wait for its normal owner update.

Rollback: existing safe-off/readbacks, preserve journal and options, install
exact b9 and restart. B9 retains the new source adapter and identical capture
format, while removing this response candidate. Restore control intent only
after existing checks; keep missing-source and unknown-confidence fallback.
No full backup restoration is needed. B8's provider incompatibility requires
the additional rollback steps described in b9's notes if used instead.

This remains experimental, not unattended stable hardware approval. The
seven-day Sprint-2 planning acceptance window has not started. Full field
evaluation and trustworthy forecast/demand prerequisites remain open.
