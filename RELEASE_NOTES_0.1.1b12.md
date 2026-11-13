# SunEnergyXT EMS v0.1.1b12 — experimental late forecast owner recovery

Selected Forecast.Solar sources now bind when their provider finishes loading
after EMS setup, including while EMS control is off. The existing runtime timer
checks only cache ownership before the ordinary control path. No provider
refresh, new timer, device polling, second writer or bypassed control gate.
A closed planner cannot attach new listeners.

An existing cache does not acquire a fresh receipt on attachment; the consumer
waits for a genuine future successful owner update. Unknown uncertainty and
PV energy stage still prohibit priority; demand gaps remain explicit.

Checked experimental installation: record options/intent and journal, use
existing safe-off and independent direct actuator-minimum readbacks, install
exact b12 and restart once. Check Supervisor readiness before requesting restart;
if a backup freeze rejects the request, wait for explicit Thaw rather than
forcing it. Confirm loaded version, off-state source binding, unchanged options,
journal/gaps, minima and fresh existing sources before restoring original intent.

Rollback: same safe-off/readbacks, exact published b11, one actual restart and
independent verification. Preserve journal and options; the capture format is
unchanged. Mark restart gaps, never restore a whole backup or invent missing
observations. B11 lacks late-owner recovery but retains prior controls.

This remains experimental. No unattended stable hardware approval, savings
certification or start of seven-day Sprint2 planning acceptance follows.
