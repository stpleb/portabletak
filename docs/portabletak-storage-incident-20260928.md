# ASGARD storage incident

## What happened

The approved automatic filesystem repair completed. A subsequent read-only filesystem check passed, and all ASGARD runtime components started successfully.

Later, while building a separate clean development rootfs, the kernel reported USB disconnects, a lost filesystem write, and an error updating the ext4 journal. The original `/dev/sda` device disappeared; a USB device subsequently appeared as `/dev/sdb`. The underlying cause is not yet established. Possible drive, connection, or power issues need physical investigation.

## Protective actions

- The recovery backup had already been copied to the Mac and its checksum matched the cartridge copy.
- Stopped the build on that cartridge.
- Normal Stop refused because cartridge metadata could not be read. Stopped the remaining ASGARD processes by their actual process root/executable paths instead.
- Unmounted every ASGARD bind mount and then the cartridge. All unmounts succeeded.
- Left the cartridge unmounted. No second repair, format, restore, or USB power cycle was attempted.
- Continued the clean build in Node01's temporary RAM filesystem.

**ASGARD is currently offline.** The management dashboard/API remains available.

## Recovery evidence

Mac backup, stored privately in this chat's workspace:
`work/recovery/portabletak-20260927_213104.tar.gz`

SHA256:
`402db10ba6302157e6c112de39b6b7b7f29be026cab94a502df1ba42843249da`

The archive and its nested backup archives passed structural verification before repair. A successful restore onto replacement media is still a separate acceptance test. This recovery archive is not a complete bootable rootfs image.

## Fix made after the incident

PortableTAK Stop now has a host-side fallback for an unmounted, invalid, or unreadable runtime cartridge. It identifies cartridge processes using `/proc`, uses Linux process handles to avoid PID reuse races, and does not need to read or execute files on the USB. A native test stopped a temporary test chroot while preserving an unrelated process, and a repeated stop found no remaining targets.

## Next physical step

Inspect the USB connection and power arrangement with the node stopped. Preserve the original cartridge and the Mac recovery backup. Use the new blank media for isolated provisioning/recovery tests; do not overwrite the original cartridge as part of a generic build.
