# SunEnergyXT EMS v0.1.1b1

**Experimental prerelease. Hardware validation and Sprint 1 acceptance remain incomplete.**

- Record unacknowledged inverter commands and recover them on timeout or Home Assistant restart (#74, #77). A confirmed limit is not proof of actual AC output.
- Expose selected EMS configuration mappings through Home Assistant diagnostics without arbitrary configuration fields (#39, #75).
- Preserve existing battery sensor mappings and numeric limits during reconfiguration (#79, #82).

These changes passed automated and Home Assistant lifecycle tests. The minimum 24-hour single-M200 real-system test has not been completed. Do not treat this prerelease as approval for unattended hardware control. An existing entry that is intentionally off remains off.
