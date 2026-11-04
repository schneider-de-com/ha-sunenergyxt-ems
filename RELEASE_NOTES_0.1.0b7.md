# SunEnergyXT EMS v0.1.0b7

**Experimental prerelease for supervised commissioning.**

- Persist and verify pending minimum inverter limits across shutdown and reconnection. Check supported Solarman register 53 directly before accepting a hardware acknowledgement.
- Limit fine percentage commands to one supported step, at most 0.1 percentage points per increase. A confirmed off-grid readback such as 2.29% after a 2.3% request no longer stalls the next 2.4% step.
- Bypass normal ramping for emergency reductions. During startup, a fresh severe grid export sample can stop output when feed-in is disabled.

The supervised 20-minute M200 test of the controller code passed the 2.29% and 4.09% readback cases and reached 5.7%. Both inverter registers read zero after shutdown. Signed grid power ranged from +109 W to +389 W over 243 observations. Sustained regulation at the 20 W target and the full physical reconnect case remain unverified.

## Upgrade

In HACS, enable prereleases for this custom repository and select the published `v0.1.0b7` tag. Upgrade the existing entry with EMS off, restart Home Assistant if requested and verify the installed version and zero limits before supervised operation.

Evidence: [shutdown and ramp changes](https://github.com/schneider-de-com/ha-sunenergyxt-ems/pull/44), [readback fix and live trace](https://github.com/schneider-de-com/ha-sunenergyxt-ems/pull/55), [tagged upgrade tracking](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/28).
