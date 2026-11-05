# SunEnergyXT EMS v0.1.1b0

**Experimental prerelease. Full hardware validation remains incomplete.**

## Repeated export after a load drop

- Two consecutive fresh significant export readings can reach the controller sooner than the five-sample median, using the less extreme reading.
- A single outlier remains filtered. Intentional feed-in keeps its existing behavior.
- [PR #70](https://github.com/schneider-de-com/ha-sunenergyxt-ems/pull/70) includes real Home Assistant runtime regression tests reproducing the November 5 measured sequence, duplicate samples, individual transients and intentional feed-in. Tests, HACS validation and CodeQL passed.

## Supervised hardware evidence

- The candidate was observed for 20 minutes on November 5 with the M200. For 16:55–17:10 CET, 185 grid readings had an arithmetic sample mean of +25.65 W, a minimum of −23.74 W and maximum of +94.78 W. Both reported inverter limits were zero after the timed shutdown.
- The specific large abrupt load drop did not recur during this run; a live verification of the new export response remains open (#69). M80 was not exercised under this low load.
- The earlier v0.1.0b9 run recorded −486.76 W export following a large load drop and slow convergence during sustained high import (#68, #69). Keep these limitations in mind.
- Select this tagged prerelease through HACS. Existing disabled entries remain disabled.

## Update discovery

- This version advances the minor beta version to work around HACS incorrectly presenting v0.1.0b9 as newer than v0.1.0b10. The underlying ordering problem remains tracked in #28.
