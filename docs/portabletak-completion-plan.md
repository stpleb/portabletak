# PortableTAK completion plan

Updated September 28, 2026.

## Definition of finished

PortableTAK is finished when one prepared cartridge can be provisioned without
ASGARD data, moved safely between Node01 and Node02, recovered onto replacement
media, hosted through a supported Windows adapter, and reached by authorized
clients over mesh, ordinary Wi-Fi, or cellular internet. Completion also
requires tested ATAK, iTAK, and WinTAK onboarding plus repeatable installers and
recovery documentation.

## Current position

The clean ARM64 software base, independent management Python runtime, generic
CA changes, first-run staging controller, durable bootstrap journal, emergency
stop path, autoload candidate, MediaMTX ARM64 package, and per-cartridge UUID
publisher exist and have automated or isolated runtime evidence.

ASGARD is offline and unmounted after USB disconnects and an ext4 journal I/O
failure. Its recovery archive is stored on the Mac and verified at SHA256
`402db10ba6302157e6c112de39b6b7b7f29be026cab94a502df1ba42843249da`.

## Work remaining

### 1. Finish the Linux first-run product

- Implement and test the production operations behind each durable bootstrap
  phase: base verification, directory ownership, PostgreSQL initialization,
  native migrations, administrator creation, CA and certificates, RabbitMQ,
  nginx, MediaMTX, service start, and final health checks.
- Make every phase idempotent and prove restart behavior after interruption.
- Add one shared lifecycle lock and state gates to Start, Stop, backup, restore,
  safe remove, autoload, and provisioning.
- Run provisioning as a supervised background job without putting passwords in
  command arguments, logs, or status files.
- Connect the API and wizard only after the backend passes isolated tests.
- Package the host tools as an installer rather than relying on Node01's hand-built
  `/usr/local/portabletak` tree.

Estimated effort: 3-6 focused work sessions before physical acceptance.

### 2. Build and accept the first physical generic cartridge

- Identify and format the new USB by model, serial, and capacity.
- Extract the format-2 clean base and MediaMTX supplement.
- Verify the software seal, publish a fresh cartridge UUID, and record the
  resulting identifiers and checksums.
- Complete first-run setup on Node02 and test retry at selected phase boundaries.
- Test reboot/autoload, safe removal, Node01 to Node02 movement, and controlled power
  interruption on disposable media.
- Test ATAK, iTAK, WinTAK, package onboarding, QR behavior, users, video, and
  certificate trust.

Estimated effort: 2-4 sessions after the USB arrives, assuming no hardware or
power faults.

### 3. Finish recovery and backup workflows

- Add backup names/notes, readable timestamps, free-space reporting, validated
  upload/import, and configurable retention.
- Keep scheduled backups opt-in and report failures without silently deleting
  the last good copy.
- Restore ASGARD onto stable replacement media in isolation.
- Compare user records, configuration, CA/server certificate fingerprints, and
  client reconnection before declaring recovery accepted.
- Prove replacement-media recovery without reconfiguring existing TAK clients.

Estimated effort: 2-4 sessions, including client checks.

### 4. Add a supported Windows host adapter

The current clean base is ARM64. A typical Windows laptop is x86_64, so its
Linux executables cannot run that payload directly. The Windows adapter needs:

- Windows CPU and version confirmation.
- WSL2 with systemd and mirrored networking, plus Windows firewall rules.
- A tested method to attach the removable USB to WSL. Microsoft's normal
  `wsl --mount` flow does not support USB flash drives, so USB/IP attachment or
  a revised media layout must be proven on the actual laptop.
- An x86_64 OpenTAKServer/PostgreSQL/RabbitMQ/nginx/MediaMTX runtime built from
  the same verified sources.
- An architecture-safe database movement strategy. The conservative design is
  a logical PostgreSQL export on safe removal and an idempotent import into the
  destination host's local cluster, while certificates, configuration, and
  client identity remain on the cartridge.
- PowerShell attach/start/status/stop/safe-remove wrappers and WinTAK acceptance.

Estimated effort: 5-10 sessions. USB attachment and cross-architecture database
movement are the largest uncertainties.

### 5. Add remote Wi-Fi and cellular access

Recommended first implementation: an optional Tailscale adapter that advertises
only the PortableTAK service address or required service ports. It must remain
outside the core package, just like the OpenMANET adapter.

- Install the overlay client on the active host or an approved gateway.
- Advertise the PortableTAK route only while that host owns the mounted
  cartridge and service identity.
- Apply deny-by-default grants for TAK client ports and separate management
  access.
- Finish dashboard/API authentication, HTTPS, CSRF protection, login throttling,
  logout/revocation, and audit events before exposing management remotely.
- Test home Wi-Fi, phone hotspot, and cellular networks, including NAT traversal,
  relay fallback, reconnect after address changes, and loss of internet.
- Preserve mesh-only operation when no WAN is available.

Estimated effort: 2-5 sessions for the managed overlay path. A self-hosted
WireGuard or Headscale deployment would add roughly 3-7 sessions for server
operations, key lifecycle, and recovery.

### 6. Final product acceptance and release

- Run the complete matrix on Node01, Node02, Windows, ATAK, iTAK, and WinTAK.
- Validate fresh provisioning, backup/restore, replacement media, autoload,
  safe removal, power loss, video, and remote access.
- Produce a versioned installer, checksums, support bundle, user guide, recovery
  guide, and known-limitations record.
- Freeze a release only after repeating the most important tests from a newly
  formatted cartridge.

Estimated effort: 2-4 sessions after all adapters work.

## Rough overall timeline

| Milestone | Focused sessions | Likely calendar time |
| --- | ---: | ---: |
| Working generic cartridge on Node01 and Node02 | 5-10 | 1-2 weeks |
| Recovery and TAK client acceptance | 2-4 | 2-5 days |
| Windows host adapter | 5-10 | 1-2 weeks |
| Remote Wi-Fi/cellular access | 2-5 | 2-7 days |
| Final packaging and repeat acceptance | 2-4 | 2-5 days |
| **Complete product** | **16-33 sessions** | **about 3-6 weeks** |

This assumes the new USB, Windows laptop, TAK client devices, and an internet
connection are available when needed. Hardware faults, Windows USB attachment
limitations, or cross-architecture database issues could extend the schedule.

## Work that does not require the new USB

- Production backend code and simulated idempotency tests.
- Shared lock/state library and background worker.
- Management authentication and remote-access adapter design.
- Generic Linux host installer and package manifest.
- Windows preflight and WSL2 adapter scaffolding.
- Backup metadata, retention, and import validation using temporary directories.
- Documentation and automated acceptance harnesses.

Physical filesystem behavior, real reboot/autoload, safe removal, power-loss
recovery, USB movement, Windows USB attachment, and end-client reconnection
cannot be accepted without the corresponding hardware.
