# SunEnergyXT EMS v0.1.1b2

**Experimental prerelease. Hardware validation and Sprint 1 acceptance remain incomplete.**

- Discover BK215 devices from both the legacy and renamed `sunenergyxt_bk` integration (#86, #87).
- Existing configurations referencing removed device or entity IDs must be reconfigured; installation alone does not migrate them.
- This beta retains the previously published unacknowledged-command recovery and diagnostics changes.

The minimum 24-hour BK215/M200 real-system test has not been completed. An existing entry intentionally set to off stays off after an update; do not enable it merely because the package installed.
