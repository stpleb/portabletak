# PortableTAK provisioning development source

This directory contains the current first-run controller and a candidate ARM64 Linux backend. Physical USB acceptance is still required before the activation gate can open.

## Architecture

PortableTAK separates cartridge state from host integration:

- The cartridge owns its UUID, instance name, CA, certificates, users, secrets, database, backups, and provisioning journal.
- The core controller accepts exactly four first-run values: server name, timezone, administrator password, and client/truststore password.
- The injected host adapter supplies the service address and host runtime profile. Core staging contains no OpenMANET address, interface, or network file.
- The OpenMANET adapter lives in `adapters/openmanet`. It owns `10.41.250.10`, `br-ahwlan`, `tak-network.conf`, and `config/host.json`.
- `LinuxBootstrapBackend` owns Linux runtime setup while receiving a host adapter explicitly. A later Windows/WSL2 adapter can use the same controller, journal, identity, and backup contracts.

The adapter contract currently exposes identity settings and validated network configuration. It is intentionally small; lifecycle detect/status/prepare/start/stop/cleanup can move behind the same boundary as Windows work begins.

## First-run safety

The controller requires a mounted generic cartridge with a published non-nil UUID, supported metadata, an empty data layout, and the expected clean-base seal. It stages private artifacts, verifies every staged hash on retry, and records durable phase evidence without secrets.

The production candidate backend performs:

1. clean-base verification
2. private storage creation
3. PostgreSQL 16 initialization and database ownership checks
4. OTS migrations and chosen administrator creation
5. CA and server certificate generation
6. independent RabbitMQ credentials
7. MediaMTX and nginx configuration
8. host adapter network configuration
9. lifecycle start and a required-service health gate
10. final metadata publication only after health passes

Passwords enter the worker on stdin. The administrator password is never written to the journal or staging directory. The cartridge UUID assigned during publishing remains its identity through provisioning.

## Release gate

The live endpoint remains fail closed unless this host file exists:

`/usr/local/portabletak/release/first-run-enabled`

Do not create that file until the new cartridge completes native and physical acceptance. The API remains bound to `127.0.0.1:8090` until management authentication and transport protection are complete.

## Current validation

Local unit suites cover the controller, bootstrap state machine, background job, clean-base seal, publisher, Linux backend, host adapter, management authentication, lifecycle lock, and MediaMTX helper behavior. Native ARM64 testing runs inside private PID, mount, IPC, UTS, and network namespaces and never points at ASGARD.

The native pre-start test verifies the clean base, PostgreSQL, migrations, administrator, CA, RabbitMQ, nginx, MediaMTX, and OpenMANET configuration. It deliberately stubs service start and physical health. USB mounting, reboot/autoload, full services, TAK clients, backup/restore, removal, and power-loss behavior remain acceptance work for the separate cartridge.

## Product modes

The durable state model is being designed for:

- Create New
- Recover / Replace
- Reset New Team

Only Create New is implemented in the current first-run path. Recovery keeps identity and certificates; reset creates an entirely new identity. Neither should reuse raw PostgreSQL data across unlike architectures. ARM to Windows portability must use a proven logical export/import contract or a controlled runtime profile.

## Compatibility

The current `/mnt/asgard` and `/asgard` paths remain as compatibility mount points. They are implementation paths, not identity. ASGARD's working data and certificates are not part of the generic clean image.
