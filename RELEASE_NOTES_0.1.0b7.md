# SunEnergyXT EMS v0.1.0b7

**Experimental prerelease for supervised commissioning.**

- Persist and verify pending minimum inverter limits across shutdown and reconnection. Check supported Solarman register 53 directly before accepting a hardware acknowledgement.
- Start fine percentage controls with at most 0.1 percentage points, retain the startup hold, then use increases of at most 20 W after settled hardware and meter feedback. A rounded readback such as 2.29% after a confirmed 2.3% command no longer blocks progress. Positive changes remain at least 20 seconds apart.
- Bypass normal ramping for emergency reductions. During startup, a fresh severe grid export sample can stop output when feed-in is disabled.

## Commissioning evidence

On 5 November 2026, a supervised 30-minute M200 run reached the 0–50 W import band after approximately three minutes. The final fixed ten-minute window contained 106 recorder samples, with mean +14.619 W and range −0.202 to +46.481 W. Household-load peaks remained visible in the full trace. Both inverter register-53 limits were directly confirmed as zero after deliberate shutdown. Separate M80 commissioning results are linked in the release PR.

The M80 reconnect test used a directly verified 100% hardware limit with its operating switch OFF. After the Solarman connection was reloaded, the disabled EMS automatically restored 0%, confirmed by register readback. This checks recovery with output inhibited. An energized inverter power-cycle was not performed. Core restart preserved the disabled state and zero limits.

The tested Deye devices report protocol 3.2, control firmware 0325-0103-0700 and communication firmware 1426-120C, using the installed `deye_micro_53.yaml` profile. Signed Shelly net power verifies household-grid behavior. Slow inverter telemetry cannot independently reconstruct every startup AC transient. The physical cause of earlier coarse-start phase bursts remains tracked in #53.

## Upgrade

In HACS, enable prereleases for this custom repository and select the published `v0.1.0b7` tag. Upgrade the existing entry with EMS off, restart Home Assistant if requested and verify the installed version and zero limits before supervised operation.

Evidence: [startup ramp fix and live trace](https://github.com/schneider-de-com/ha-sunenergyxt-ems/pull/58), [release commissioning](https://github.com/schneider-de-com/ha-sunenergyxt-ems/pull/56), [100% reconnect test](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/32#issuecomment-6651758262), [tagged upgrade tracking](https://github.com/schneider-de-com/ha-sunenergyxt-ems/issues/28).
