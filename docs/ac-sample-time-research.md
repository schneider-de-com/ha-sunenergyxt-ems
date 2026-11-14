# Deye AC acquisition time: packet-time research (Refs #13, #92)

Investigated 2026-11-14 using public repository sources. This research queried
no devices and changed no HA configuration, logger, network, firmware or protocol.
It made no packet capture, raw archive download or contact with third parties.

## Finding

**The inspected primary sources establish no validated physical AC acquisition
timestamp or hard maximum measurement age for the installed M200/M80, protocol 3.2, control board
`0325-0103-0700`, communication board `1426-120C`, Solarman 25.08.16.**
The new lead identifies logger packet-clock diagnostics, not a demonstrated
timestamp for acquisition of the AC-power value. It therefore does not repair
the household learner's acquisition/coverage gate.

This supplements, rather than replaces, the existing
[register investigation](register-measurement-investigation.md) and
[energy-counter feasibility](energy-counter-feasibility.md). Their register,
counter and observation results are not new solutions here.

## What the dummycloud source actually establishes

Pinned repository: `chrisunderscorek/deye-microinverter-cloud-free-ha`, commit
[`a79ab1f5acfe953951f2325d85dab66ca35fdb0e`](https://github.com/chrisunderscorek/deye-microinverter-cloud-free-ha/commit/a79ab1f5acfe953951f2325d85dab66ca35fdb0e).
Its [v2 package metadata](https://github.com/chrisunderscorek/deye-microinverter-cloud-free-ha/blob/a79ab1f5acfe953951f2325d85dab66ca35fdb0e/dummycloud2/config.yaml#L1-L5)
declares version 2.0.5; the README's older image-size table is not the version
anchor used for code analysis.

- [README packet observations](https://github.com/chrisunderscorek/deye-microinverter-cloud-free-ha/blob/a79ab1f5acfe953951f2325d85dab66ca35fdb0e/Readme.md#L229-L258)
  separate `0x42 DATA` telemetry, `0x43 WIFI` status and `0x48 REPORT`. The
  observed REPORT is 60 bytes, with an uptime-like counter, base time plus
  offset, status bytes and reserved-looking data. The author describes logger
  liveness/reboot/clock uses and reports no inverter energy telemetry there.
- [Rust decoder](https://github.com/chrisunderscorek/deye-microinverter-cloud-free-ha/blob/a79ab1f5acfe953951f2325d85dab66ca35fdb0e/dummycloud2/src/protocol.rs#L67-L103)
  calculates REPORT time as `base_timestamp_seconds + timestamp_offset_seconds`.
  [Parsing](https://github.com/chrisunderscorek/deye-microinverter-cloud-free-ha/blob/a79ab1f5acfe953951f2325d85dab66ca35fdb0e/dummycloud2/src/protocol.rs#L188-L223)
  places these counters in REPORT/WIFI structures; neither carries the AC value.
- [DATA decoding](https://github.com/chrisunderscorek/deye-microinverter-cloud-free-ha/blob/a79ab1f5acfe953951f2325d85dab66ca35fdb0e/dummycloud2/src/protocol.rs#L291-L334)
  reads grid active power at payload offset 59. The published DATA model/decoder
  does not attach an acquisition timestamp to that value. Some payload bytes
  remain uninterpreted; this is not proof that no useful field could exist.
- [Dispatch](https://github.com/chrisunderscorek/deye-microinverter-cloud-free-ha/blob/a79ab1f5acfe953951f2325d85dab66ca35fdb0e/dummycloud2/src/server.rs#L204-L238)
  handles DATA and REPORT separately.
  [MQTT reporting](https://github.com/chrisunderscorek/deye-microinverter-cloud-free-ha/blob/a79ab1f5acfe953951f2325d85dab66ca35fdb0e/dummycloud2/src/mqtt.rs#L271-L298)
  publishes logger report time and derives last reboot by subtracting uptime.
  No demonstrated association turns this into the AC value's acquisition time.

Inference: a valid packet clock could help identify logger restarts or delayed
status records. Establishing AC age additionally requires proof of when the
inverter refreshed its AC register and how that event binds to this packet.
Reception time, uptime and packet-generation time do not supply that proof.

## Applicability and the installed polling path

The fork's [device list and firmware note](https://github.com/chrisunderscorek/deye-microinverter-cloud-free-ha/blob/a79ab1f5acfe953951f2325d85dab66ca35fdb0e/Readme.md#L44-L78)
include SUN600G3, M80G3 and M80G4. Its logger discussion uses MW3 firmware
identifiers; it does not validate the locally labelled M200 device or establish
a correspondence to the exact installed board-version tuple above. The list
supports a research lead for M80, not firmware-specific timestamp validation for
either installed device.

Solarman tag v25.08.16 resolves to commit
[`e8db5da48a348794be86c5d9780a2468272b71cb`](https://github.com/davidrapan/ha-solarman/commit/e8db5da48a348794be86c5d9780a2468272b71cb).
Its bundled [Solarman V5 implementation](https://github.com/davidrapan/ha-solarman/blob/e8db5da48a348794be86c5d9780a2468272b71cb/custom_components/solarman/pysolarman/__init__.py#L154-L169)
recognizes REPORT/DATA/INFO side frames and responds with the host's current
Unix time; the comment explicitly leaves future payload processing open.
The [register-response path](https://github.com/davidrapan/ha-solarman/blob/e8db5da48a348794be86c5d9780a2468272b71cb/custom_components/solarman/pysolarman/__init__.py#L265-L296)
removes the V5 envelope and returns parsed Modbus words, without exposing a
physical AC sample time. Its local
[device-update clock](https://github.com/davidrapan/ha-solarman/blob/e8db5da48a348794be86c5d9780a2468272b71cb/custom_components/solarman/device.py#L11-L31)
uses `datetime.now()` on successful integration updates. These are transport/
integration semantics, not AC acquisition guarantees.

[Issue #84](https://github.com/davidrapan/ha-solarman/issues/84) initially concerns
a Deye 600 and repeated values despite changed polling settings. The owner's
[2026-01-22 comment](https://github.com/davidrapan/ha-solarman/issues/84#issuecomment-3783283038)
reports old microinverter data updating at five minutes, sometimes one minute,
regardless of request interval. This supports the stale-data concern. It is
neither manufacturer certification for this firmware nor a hard five-minute
age bound covering stalls, reconnects, reboots and cached data.

## Next legitimate evidence

First seek an existing manufacturer/maintainer protocol description or sanitized
public test fixture that binds an **AC register acquisition event or sequence**
to a specific payload time for the exact installed model/firmware. The required
contract must distinguish inverter sampling, logger caching, packet creation,
clock synchronization/reset and HA reception, and specify behavior during
reconnects or stale-register responses. A clarification can be drafted without
sending it; none was sent here.

An already available, consented and sanitized capture could test consistency of
that proposed mapping; matching packet times alone would still not prove the
physical acquisition event or a worst-case lag. No new capture, cloud endpoint
replacement, TLS workaround, firmware downgrade or logger reconfiguration is
authorized by this note. Until a defensible acquisition-time/age contract is
established, preserve unknown age and learning gaps; do not reinterpret a fresh
sensor report, changed value or five-minute observation as complete AC coverage.
