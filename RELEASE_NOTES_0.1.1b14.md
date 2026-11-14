# SunEnergyXT EMS v0.1.1b14 — keep pending forecast writes unconfirmed

b13 could briefly expose a newly received forecast row as persistent while its
save/readback was still running. Mark the history unconfirmed before awaiting
the first I/O operation, and confirm it only after both operations complete.
The same status and explicit uncertainty apply during a delayed readback.

No control decisions, hardware commands, provider/device polls, operating
options or storage schema change. Historical records never restore live
freshness or confidence. Tests pause both save and readback, including with a
native HA Store, and inspect the intermediate state before allowing completion.

Experimental rollout: preserve options, original enabled intent, demand and
forecast journals; existing safe-off and independent minima readbacks, checked
Supervisor readiness, one actual restart, then loaded-version/history/options
readbacks before normal intent restoration. Exact published b13/f87f45c is the
rollback; both versions share the forecast Store format. No full restore or
direct `.storage` edit. Rollback retains the old transient reporting limitation.

This fix does not establish a real forecast write, calibrated PV/demand input,
hardware convergence, savings or Sprint-2 acceptance. First real archive evidence
and the seven-day planning window remain subject to their separate checks.
