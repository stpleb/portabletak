PortableTAK 1.2 is a small update after 1.1. It stops deleted TAK Server feeds from appearing as data packages, widens the Mac and Windows name lookup fix, and adds GPS time for nodes.

**Fixed**
- When a feed is deleted on TAK Server, its archive copy is no longer turned into a data package on PortableTAK.
- Every service on Mac and Windows now gets the computer's DNS settings at start-up. In 1.1 this fix only covered the aircraft and ship feed.

**New**
- A node can set its clock from GPS when no other time source is available.

**Tested**
- Automated tests: 16 of 16 for federated files, and the node clock tests.
- Fresh-drive build test: 7 of 7 services.
- Installed on two real nodes.

**Not yet tested**
- The archive fix on a live TAK Server link.
- The GPS clock with a real GPS fix.
- The aircraft feed on a real Windows computer.
- A larger TAK Server network with several servers, and Raspberry Pi hosts.

**Build:** 2026.10.30.4.
