# PortableTAK progress update

## Current state

ASGARD is offline and its original USB is unmounted. The kernel reported USB
disconnects, a lost write, and an ext4 journal I/O failure during development.
All processes that depended on the cartridge were stopped and every bind mount
was unmounted. No further repair, format, or restore was attempted.

The latest recovery archive remains on the Mac with SHA256:

`402db10ba6302157e6c112de39b6b7b7f29be026cab94a502df1ba42843249da`

## Completed work

- Built a fresh ARM64 software base from Ubuntu Base 24.04.5 and official
  Ubuntu/PyPI packages. No ASGARD rootfs or private data was copied into it.
- Added archive traversal and link checks, clean-tree identity scanning,
  package provenance, and a full content manifest.
- Removed dependency test fixtures, including a sample private key that the
  clean-tree audit correctly detected.
- Rebuilt the clean base with a format-2 software seal so every physical
  cartridge can receive a unique UUID without invalidating its software
  integrity record. The template nil UUID is refused by provisioning until the
  one-time publisher replaces it. New seal SHA256:
  `df9a6ffb775712f540a1afab28fc369f441c193096bfab846c862e9de51ff775`.
- Saved the verified 491 MB format-2 clean-base archive on the Mac. Archive
  SHA256: `42904660fa1efca94db9ab17558339098023311d9277a146916944159170a56b`.
  The original fixed-identity archive remains as a build record and is marked
  unsuitable for copying to multiple cartridges.
- Added a durable bootstrap state engine. Its eight tests cover successful
  completion, failed-phase retry, interrupted-operation replay, secret and
  artifact protections, and the final metadata/journal crash window.
- Packaged the OpenTAKServer-compatible MediaMTX 1.13.0 ARM64 release after
  verifying its official SHA256. Its configuration and localhost API started
  successfully on Node01, and the temporary process was stopped afterward.
- Replaced MediaMTX command-line token hooks with a tested Python relay that
  reads the token from a restricted file.
- Added management authentication primitives: scrypt password verification,
  a separately derived signing key, bounded signed sessions, constant-time
  comparisons, tamper checks, and expiry checks. Five tests pass. The live API
  remains localhost-only; remote binding is still disabled.
- Added one shared host lifecycle lock for provisioning, autoload, Start, Stop,
  backup, restore, and safe remove. Provisioning, identity publication, and the
  autoload candidate now use its lock path. Four concurrency and cleanup tests
  pass, and the BusyBox autoload test passes again on Node01.
- Added a bootstrap job supervisor that sends passwords to a worker only over
  stdin, records no request secrets, detects interrupted workers, refuses
  concurrent jobs, and checks completion ownership. Four tests pass.
- Saved the current reviewed provisioning source as
  `portabletak-provisioning-stage2-source.zip`, SHA256
  `12d7bd445f64569640a730d6088753bf385355e3c0cb45ce3603f366bdfea475`.
- Saved the MediaMTX supplement as
  `portabletak-mediamtx-1.13.0-linux-arm64.tar.gz`, SHA256
  `8fc6fad1acd075218f65f0908009f3791f1a7b50ad6af1b0467cfdd2a8512964`.
- Ran a private-network integration test from a separate mutable copy. It
  passed staging, fresh PostgreSQL 16 initialization, native migrations,
  selected administrator creation, rejection of the upstream default password,
  fresh CA generation, independent RabbitMQ credentials, OpenTAKServer startup,
  nginx TLS, CA-chain validation, and the `10.41.250.10` certificate identity.
- Rebuilt and tested client certificate/package generation from the verified
  upstream OpenTAKServer wheel. Certificate names are now constrained to safe
  filename characters; OpenSSL receives passwords through environment variables.
- Installed a private FastAPI/uvicorn dependency bundle for the management API.
  The live API no longer imports from `/opt/cicadatak/lib`; dashboard and status
  routes pass on `127.0.0.1:8090`.
- Installed a host-side emergency Stop fallback. It can stop processes rooted
  on an unreadable or missing cartridge without reading or executing USB files.
  Its native process test preserved an unrelated process and repeated safely.
- Built an autoload candidate that starts `existing` or `provisioned`
  cartridges and leaves `new`, `provisioning`, `failed`, or `recovery` media
  stopped for the appropriate workflow. Its seven-state test passes on Node01.
  It is not installed while the ASGARD USB connection is unstable.

## Verification summary

- An 81-test local Python selection passes for the controller, clean-base seal,
  identity publisher, bootstrap engine and supervisor, lifecycle lock,
  management authentication, MediaMTX, emergency stop, and build protections.
- Ten ASGI route tests pass on Node01 using the independent API dependency bundle.
- 10 ASGI route tests pass on Node01 using the independent API dependency bundle.
- Native emergency process shutdown test passes.
- Native clean runtime integration passes in private PID, mount, network, IPC,
  and hostname namespaces.
- Native CA, server/client certificate, ATAK/iTAK package, custom-password, and
  service-IP SAN test passes against the clean upstream environment.
- Live dashboard, status, and provisioning-status endpoints return HTTP 200.
- Stop while storage is unmounted reports zero remaining cartridge processes.

## Still pending

- Production platform operations for the durable bootstrap engine and physical
  health-gated completion. Lock and worker foundations now exist, but the live
  shell controllers and API have not been switched to them.
- Install MediaMTX into the final cartridge payload and exercise live stream
  publish/read behavior with OpenTAKServer and TAK clients.
- Deploying the wizard after the production backend is ready.
- Physical USB layout and first-run acceptance on the new blank cartridge.
- ATAK, iTAK, WinTAK, QR/package onboarding, reboot/autoload, safe removal,
  Node01/Node02 movement, power-loss recovery, and broader restore acceptance.
- Recovery of ASGARD onto stable media and verification of users, configuration,
  certificate fingerprints, and existing client reconnection.

The clean base is a verified software input. It is not yet a finished or
provisionable cartridge image.
