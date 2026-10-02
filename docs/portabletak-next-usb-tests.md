# Tests when the blank USB arrives

## Before testing

1. Keep the original ASGARD cartridge and the checksum-verified Mac recovery backup intact.
2. Identify the new USB by model, serial, capacity, and device path before any formatting. `/dev/sda` is not a stable identity; the failing device reappeared as `/dev/sdb`.
3. Check Node01's USB connection and power. Node02 remains the preferred isolated host for fresh-cartridge tests.

## Provisioning acceptance

The production bootstrap, lifecycle locking, background jobs, and final health gate must be complete before calling this an acceptance run. Do not mistake a staged wizard response or an isolated runtime test for completed provisioning.

- Start with a verified clean software payload and empty persistent directories.
- Verify the format-2 base seal, run the one-time identity publisher, and record
  the fresh cartridge UUID. Confirm the nil template UUID cannot be staged and
  the publisher refuses a second identity assignment.
- Choose an instance name, timezone, administrator password, and separate client-package password.
- Confirm a new cartridge identity, independent secrets, a fresh CA, server certificate, empty database, and initial administrator.
- Confirm the chosen administrator works and the upstream default password fails.
- Test interruption/retry at each bootstrap phase without silently replacing issued keys or credentials.
- Confirm every required service is healthy before marking provisioning complete.
- Confirm completed cartridges cannot enter Create New again.

## Recovery and physical acceptance

- Restore the preserved recovery backup to isolated replacement media and compare users, configuration, and certificate fingerprints.
- Reconnect existing clients without changing their server identity or trust configuration.
- Test ATAK, iTAK, and WinTAK package onboarding and the documented QR onboarding behavior.
- Test backup, safe removal, movement Node01 ↔ Node02, and reboot/autoload.
- Only after successful backup/recovery, test controlled unexpected power loss on disposable test media.

Record actual results and device identities. Do not use ASGARD's original cartridge for destructive acceptance tests.
