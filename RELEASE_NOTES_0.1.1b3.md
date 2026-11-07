# SunEnergyXT EMS v0.1.1b3 — experimental beta

- Add individual battery state-of-charge, discharge-limit and DC-output diagnostics.
- Distinguish the last limit passed to Home Assistant, the limit reported by its
  entity, and an optional AC power source. A successful service call does not
  confirm device acceptance or measured AC power.
- Hide obsolete PID values during grid loss, recovery and safety conditions.
  AC readings without a recent Home Assistant state change are unavailable;
  a state change does not establish the time of the physical device sample.
- No new control writes or device polling are added for diagnostics.

This experimental build requires validation against the actual installation.
