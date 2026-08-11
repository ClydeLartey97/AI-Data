# Facility hardware discovery

`hardware/providers.py` currently proves what one host is (`LocalDetector`)
or loads a fleet somebody typed (`SimulatedFleetProvider`). A facility-scale
provider must inventory what is actually connected — servers and their BMCs,
GPU fleets, PDU/UPS power, network topology — without a hand-maintained file.

The rule that governs everything below is inherited unchanged from
`hardware/base.py` and `docs/calibration.md`: **detection is not a
benchmark.** Auto-discovery can prove identity, installed memory and an
instantaneous power reading. It can never promote throughput or a power
curve to `MEASURED`; calibration remains the only path there.

## What discovery can prove

| Field | Best provenance | How | Notes |
|---|---|---|---|
| Identity (model, vendor) | MEASURED | Redfish, DCGM, SNMP, LLDP | The device's own self-report, read directly |
| Installed memory | MEASURED | Redfish `MemorySummary`, DCGM | |
| Live power | MEASURED | Redfish power sensors, DCGM, PDU SNMP | One observation, not a curve — same caveat as `nvidia-smi` today. Carries a measurement scope (below) |
| Topology (what connects to what) | MEASURED | LLDP, Redfish chassis links | |
| Throughput / power curve | never above catalogue | — | Catalogue `SPEC`/`ESTIMATED` by model match, else `UNAVAILABLE` |

A discovered device with no catalogue match stays in the inventory with
`UNAVAILABLE` performance and is named in a warning when a `Fleet` is built
from the result. It is not silently dropped and not silently invented.

## Address scope: enumeration is not scanning

The operator declares the endpoints — explicit hosts, or an explicit list.
The provider queries the declared protocol on the declared endpoints and
nothing else. It never sweeps a subnet, never port-scans, never follows a
referral outside the declared scope. Passive listening (LLDP frames, SSDP
announcements) is acceptable because nothing is transmitted; active probing
of undeclared addresses is not, in any tier.

## The three access tiers

### Tier 0 — safe automatically (read-only, anonymous by protocol design)

- **Redfish service root.** `GET /redfish/v1/` (and `/redfish`,
  `/redfish/v1/odata`, `/redfish/v1/$metadata`) must be served without
  authentication under DMTF DSP0266. It identifies the vendor, product and
  protocol version — enough to know *what is there*, nothing more.
- **Passive LLDP listening.** Switches broadcast LLDP on a fixed interval;
  listening transmits nothing. Capture needs an OS privilege; if that
  privilege is absent the feature is off — the collector never escalates,
  mirroring the `powermetrics` fail-closed rule in the Apple protocol.

SNMP with the default `public` community is **not** tier 0. A community
string is a credential; trying vendor defaults is credential guessing.

### Tier 1 — explicit operator-supplied read-only credentials

- **Redfish authenticated inventory**: Systems, Chassis, Memory, Processors,
  PCIeDevices, Power/PowerSubsystem sensors. Requires an operator-created
  account with a read-only role. Basic or session authentication over TLS
  (the first implementation sends HTTP Basic; the credential is read from an
  environment variable and never written to config or logs). Accepting a
  self-signed BMC certificate is an explicit per-endpoint pin, never a
  global verify-off switch.
- **SNMP v2c/v3** for PDU and UPS power: GET/GETNEXT/WALK only. v3 with
  authentication preferred; v2c community accepted because most installed
  PDUs speak nothing newer.
- **NVIDIA DCGM**: local socket on a GPU node, or a remote `nv-hostengine`.
  DCGM has no authentication of its own, so a remote endpoint is treated as
  credentialed-equivalent: it must be individually declared and is expected
  to live on a management network.
- **IPMI**: legacy fallback only where a BMC has no Redfish. Sensor and FRU
  reads only. The protocol's session crypto has known weaknesses; it is
  never used across an untrusted network.

### Tier 2 — never touched automatically

- Any write or action: power control, boot order, firmware, BMC account
  management, SNMP SET, DCGM configuration or policy writes.
- Credential guessing, including vendor defaults.
- Scans or probes outside the declared endpoint list.
- Storing or serialising raw serial numbers, UUIDs, MAC addresses or asset
  tags (see next section).

## Identity without identifiers

Fleet inventory needs a stable per-device key so repeated polls update one
record instead of duplicating it — but the existing rule (no retained
serials, UUIDs or host identifiers in `LocalDetector`) stands. Resolution:
the device key is a keyed one-way digest (HMAC-SHA256) of the
protocol-native durable identifier, using a site key generated locally and
stored outside version control. The raw identifier is discarded at read
time; only the digest is stored or serialised; the site key never leaves
the machine. Rotating the site key re-keys the inventory and breaks joins
to prior history — a documented cost, chosen by the operator.

## Power measurement scope

Three different instruments measure three different things. Each discovered
power figure carries its scope, and scopes are never compared as if equal —
the same discipline as energy methods in `workload-evidence-v1`:

- `board` — DCGM GPU board power: the accelerator alone;
- `system` — Redfish chassis/PSU input power: CPUs, fans, PSU losses included;
- `outlet` — PDU per-outlet power: everything plugged into that outlet,
  wall-side of the PSU.

None of these is facility power; PUE still applies downstream.

## BMC load discipline

BMCs are slow embedded controllers and an aggressive poller is an
operational hazard in itself. Rules: requests to one endpoint are serial;
concurrency across endpoints is bounded; every request has a timeout; polls
have a minimum interval and a per-poll request cap; a failing endpoint gets
exponential backoff. Any endpoint error yields a partial inventory with a
warning naming the endpoint — never a hang, never a retry storm, never a
credential in a warning string.

## Protocol order

**Redfish first.** The selection logic is the same one that picked GB as
the first market adapter: the clearest read-only path to a real signal,
with the fewest credentials and dependencies.

1. Standardised read-only inventory is the protocol's purpose. DSP0266 is
   the designated successor to IPMI and covers servers, chassis, memory,
   processors/accelerators and power sensors in one contract.
2. The unauthenticated boundary is defined by the standard itself — the
   service root must be anonymous, everything deeper is credentialed — so
   the tier design maps onto the protocol rather than being invented
   around it.
3. Zero new dependencies: HTTPS and JSON from the standard library, like
   the keyless market adapters. `ipmitool`, `lldpcli` and `dcgmi` are all
   absent from the development machine; Redfish needs nothing installed.
4. It is testable honestly without a data centre — see the ladder below.

Then, in order: **DCGM** (richest GPU signal — identity, memory, live board
power per GPU — but requires NVIDIA hardware, none reachable here);
**SNMP** (real wall-side power from PDUs; requires operator community
strings and installed PDUs); **LLDP** (topology enrichment, not inventory;
requires capture privilege and a managed switch).

## Verification ladder

Each stage states exactly what it proves, mirroring the offline-first,
then-live pattern used for `gb.py`:

1. **Offline unit tests** (`tests/test_hardware_providers.py`) against
   DMTF's published mockup trees (DSP2043) — real vendor-shaped payloads,
   not hand-invented fixtures. Proves parsing, provenance mapping and
   identifier hygiene.
2. **Local HTTP run** serving a DSP2043 tree — a real network path with
   real pagination and error handling. Proves protocol handling, not
   facility truth.
3. **OpenBMC under QEMU** — actual BMC firmware with a real Redfish
   service and real session authentication. Proves the code against a
   genuine implementation's quirks. Still not real iron.
4. **A physical BMC** — the first real facility signal. None is reachable
   from the current development machine; stages 1–3 are the honest limit
   until one is. Nothing above stage 3 may be claimed before stage 4 runs.

## Declared-endpoint configuration

```json
{
  "schema": "facility-discovery-v1",
  "endpoints": [
    {
      "protocol": "redfish",
      "host": "10.0.10.21",
      "port": 443,
      "tls_fingerprint_sha256": "…",
      "credential_env": "REDFISH_RO_CREDENTIALS",
      "poll_seconds": 300
    }
  ]
}
```

Credentials are referenced by environment-variable name, never written into
the file. The file is local configuration, excluded from version control
like calibration profiles. An endpoint may set `"tls": false` for loopback
hosts only — the path used by ladder stages 2 and 3 — and the provider
rejects plain HTTP to any non-loopback address at construction time. Each
poll is further bounded by a per-endpoint request budget; exhausting it
yields a partial inventory with a warning, never an unbounded walk.

## Out of scope for the first provider

DCGM, SNMP and LLDP collectors; automatic address discovery of any kind;
any control-plane action. Each later collector gets the same tier analysis
before implementation.
