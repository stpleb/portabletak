# PortableTAK provisioning, stage 1

> Historical stage-1 record. For the current state after clean-base assembly,
> independent API dependencies, native runtime integration, and the USB
> incident, see `portabletak-progress-20260928.md`.

This is an incomplete development implementation, not a distributable cartridge image.

## Installed on Node01

- `bin/portabletak-provision`: status, input validation, and private staging.
- `lib/portabletak_provision.py`: standard-library controller.
- `api/provision_routes.py`: GET `/api/provision/status`, POST `/api/provision/validate`, and guarded POST `/api/provision`.
- The live API includes the router. Its listener remains 127.0.0.1:8090.

POST `/api/provision` cannot activate a server in this version. It returns 409 for an existing cartridge or an unavailable backend. The stage command writes only staging artifacts and an incomplete provisioning state. It is intended for controlled development targets. It is not a complete provisioner, does not create a usable administrator account, and never marks a cartridge complete.

The live dashboard, lifecycle scripts, OTS installation, CA, users, database, and network configuration were not replaced. Only the management API was restarted.

## Development components kept separate

- `web/index.html`: complete candidate dashboard with the first-run view. It preserves the original styling. Not installed on Node01.
- `web/wizard.html` and `web/provision.js`: source for the added view. Settings can be checked, but activation stays disabled.
- `lib/build_ca_module.py`: transforms the reviewed native OTS CA source into a generic-only module. It checks a source SHA-256 and expected syntax structure.
- `lib/generic_certificate_authority.py`: generated generic CA candidate. Never install over ASGARD's native module. It relies on OTS's existing CA templates and package layout.
- `lib/bootstrap_admin.py`: creates the administrator in a migrated empty OTS database. Its caller must verify that the connection belongs to the new instance. It refuses any existing user database.

The CA adaptation uses argument-safe OpenSSL execution, environment password sources, separate client export credentials, XML-escaped password preferences, larger random serial numbers, and a service-IP SAN. Native CA/package behavior is otherwise retained, including certificate directory replacement on explicit issuance. The future controller must own and validate its staging paths before invoking it.

## Staging contract

A mounted cartridge must have format-1 metadata, state `new`, instance type `generic`, and a unique cartridge UUID. It must have no existing config, PostgreSQL, RabbitMQ, or OTS data. Staging also requires `portabletak/base.json`:

```json
{"format": 1, "kind": "portabletak-clean-base", "architecture": "arm64"}
```

This declaration is a development precondition, not a cryptographic certification or a substitute for the pending clean-base audit.

Input is JSON on stdin with exactly `instance_name`, `timezone`, `administrator_password`, and `client_password`. Do not pass passwords in shell arguments. Timezones are checked against the cartridge's zoneinfo when available. The administrator password is validated but not persisted during staging. Re-enter it for the future bootstrap phase.

Staging preserves the original metadata, binds a journal to the source cartridge UUID, generates one new instance UUID and independent secrets, and verifies artifact hashes on retries. A retry cannot silently change the instance name, timezone, or client password. Private files are mode 0600 inside mode-0700 directories. A failed stage stays incomplete. There is no reset/reprovision operation.

## Validation performed

- 25 controller tests and 10 ASGI route tests passed on Node01.
- 5 JavaScript behavior tests passed locally.
- Native OTS CA/server/client/package test passed in temporary files.
- Native OTS migrations and administrator test passed in a separate PostgreSQL 16 cluster with no TCP listener.
- Live API smoke checks verify that the original endpoints work and that ASGARD provisioning is refused.

The SQLite migration attempt failed at a PostgreSQL sequence migration, so PostgreSQL was used for the successful native test. The installed driver is psycopg 3; generated configuration explicitly uses `postgresql+psycopg`.

Tests can be selected individually. Native tests require the OTS environment. `test_admin_native.py` must run as the postgres OS user and creates/stops only its own temporary cluster. It preserves cluster files if shutdown fails. No native test should be pointed at ASGARD's configuration.

## Deployment record

API backup: `/usr/local/portabletak/api/app.py.before-provision-stage1-20260927`.
Recovery backup: `/mnt/asgard/portabletak/backups/recovery/portabletak-20260927_213104.tar.gz`.

Full clean-base assembly, runtime orchestration, lifecycle coordination, background jobs, all-phase recovery, and health-gated activation are still pending. Hardware acceptance will use a new USB cartridge on Node02. See the accompanying roadmap for the complete backlog.
