# PortableTAK first-run implementation plan

## Inspection result

Inspected Node01 on September 27, 2026. ASGARD remains RUNNING with PostgreSQL, RabbitMQ, MediaMTX, OTS, CoT, EUD, and nginx reporting RUNNING. One CoT client is connected. The management API listens on 127.0.0.1:8090.

Created `/mnt/asgard/portabletak/backups/recovery/portabletak-20260927_211010.tar.gz` (200 KiB). The existing verifier accepted the backup and PostgreSQL dump. Both nested config and OTS archives passed archive listing checks. This is structural verification, not a new restore acceptance test. No services were restarted and no production source/configuration was edited.

## Findings that affect implementation

- Detection accepts `new`, `existing`, `provisioned`, and `recovery`. It rejects `provisioning` and `failed`. Startup accepts every state that detection accepts, including `new`.
- Lifecycle tools use fixed paths, shared host ports, and broad process checks. A second chroot alone cannot safely isolate a test instance from ASGARD.
- OTS `create_app(cli=True)` provides a useful bootstrap path. Normal initialization runs migrations, creates a CA, connects to RabbitMQ, and starts scheduling. Default administrator creation occurs later in `main()`. Bootstrap should migrate and create the administrator with its chosen password before normal startup.
- Native CA code builds shell commands and can log them at debug level. Client PKCS#12 files and package preferences currently use the CA password. Generic provisioning requires safe subprocess arguments, secret-safe diagnostics, and a distinct client export password in the generic runtime.
- Startup contains a fixed service-IP listener check and a legacy network-status key mismatch. Advanced address support must update the relevant checks together.
- The inspected autoload script and init hook detect/mount the cartridge; they do not start the TAK services. Reboot acceptance remains pending.
- Node01 shows the ASGARD USB and its system storage, with no second USB test cartridge visible.

## Proposed stages

### 1. Build and test the controller separately

Create `portabletak-provision` with Python standard-library orchestration, JSON input over stdin, and structured secret-free status. Develop in a separate workspace first.

Use `new -> provisioning -> provisioned`, with `failed` on error. Preserve `existing` compatibility. Reject existing/provisioned/recovery cartridges for Create New. Validate the actual mounted device, target paths, base manifest, architecture, and absence of prior instance data. Reject symlink escapes and ambiguous metadata.

Use an OS lock shared with conflicting lifecycle actions, an atomic durable journal, phase checkpoints, and restricted staging permissions. Bind retries to the same cartridge and attempt. After interruption, reconcile recorded artifacts before retrying; never silently replace a completed identity. Request passwords again when required rather than persisting the administrator password.

### 2. Define the clean base and bootstrap

Use a reviewed allowlist of software and generic templates. Never clone ASGARD data or its rootfs wholesale. Exclude users, databases, CA material, logs, cached packages, RabbitMQ state/cookies, machine identities, and embedded credentials.

Generate a new UUID and independent application, PostgreSQL, CA, RabbitMQ, and MediaMTX secrets. Build a clean PostgreSQL cluster and OTS database using local administrative sockets. Run native migrations, create native roles, and create/hash the chosen administrator password before any public application listener starts. Verify the chosen password and reject the default credential.

Reuse native CA behavior with a narrowly scoped patch in the generic runtime for safe subprocess calls and separate truststore/client export credentials. Verify generated certificate chains, key matching, service-IP SANs, and package passwords. Generate generic HTTPS, nginx, MediaMTX, and network configuration. Keep compatibility paths such as `/asgard` initially. Timezone belongs to the cartridge/runtime; do not change host timezone.

### 3. Add minimal API and wizard integration

Add `GET /api/provision/status` and `POST /api/provision`, with a persistent controller job so browser disconnects do not cancel provisioning. Add bounded input validation, same-origin request checks, and secret-free error responses.

Collect instance name, IANA timezone, administrator password, and truststore/client password. Default network identity to 10.41.250.10/32 on br-ahwlan. Add advanced network fields only with matching lifecycle/client checks.

Show the wizard for recognized incomplete states and the existing dashboard for completed states. Distinguish missing/invalid media from unprovisioned media. Preserve current styling. Gate startup and conflicting API operations during provisioning, while retaining safe failure cleanup/removal.

### 4. Validate and activate only on an isolated test target

Test state transitions, concurrent actions, invalid inputs, refusal of existing cartridges, interrupted phases, retries, secret handling, and absence of inherited identity. Run shell syntax checks and Python compilation after changes.

Full service tests require a separate supported node/test cartridge or verified process/network/mount isolation. Do not launch the current lifecycle scripts against a second directory on Node01. A concurrent mesh test must use a different unoccupied service IP from ASGARD.

After bootstrap succeeds, allow only controller-owned validation startup while the state remains provisioning. Mark provisioned only after all required service and identity checks pass. On failure, stop only attempt-owned services, preserve the journal/staging, and keep normal startup blocked.

Then validate user creation, ATAK/iTAK/WinTAK packages, a real client, backup/restore, reboot/autoload, and movement to a second node. Report physical tests separately until performed.

## Approval boundary

The handoff explicitly requires plan approval before implementation. Approval starts the separate controller and tests, followed by incremental integration with per-file backups. Test media/node selection is required before full runtime acceptance. Reset/reprovision, management authentication, dependency vendoring, and broad UI/path cleanup remain outside this first implementation.
