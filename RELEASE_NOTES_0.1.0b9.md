# SunEnergyXT EMS v0.1.0b9

**Experimental prerelease. Unattended inverter control has not been verified.**

## Capacity-weighted battery level

- The central EMS device exposes the aggregate state of charge weighted by each battery system's total capacity, including expansion modules.
- Configure capacities in Wh for each system. The sensor is unavailable if any configured capacity or battery reading is missing, invalid or stale.
- English and German capacity field labels are available for the first ten systems; additional labels are tracked in #64.

## Evidence

- [PR #63](https://github.com/schneider-de-com/ha-sunenergyxt-ems/pull/63): 276 local tests, including a Home Assistant lifecycle test with unequal capacities, changes and unavailable/stale inputs. GitHub test, HACS validation and CodeQL checks passed.
- On the connected system the candidate was installed through HACS: with 4,300 Wh and 2,150 Wh configured and both batteries reporting 100%, the central sensor reported 100%. The EMS remained off during this check.
- Supervised live control and shutdown observations from v0.1.0b7 do not establish hardware verification of later PID changes in v0.1.0b8 or this release. Outstanding hardware investigations are tracked in #27, #31, #32, #47 and #53.
- Select this tagged prerelease in HACS. Existing configurations retain their disabled state after update.
