PortableTAK 1.1 fixes live aircraft and ships on Mac and Windows setups, and improves federation with an official TAK Server.

**Fixed**
- Aircraft and ships from the internet now work on the Mac version. The feed could not look up internet names there. The same fix is in the Windows version.
- Data Sync feeds created in the ADS-B or AIS channel now federate. ATAK can pick those channels by default, so the server moves such a feed to a channel the user can send to, preferring Public. ATAK may still show ADS-B on the create screen.
- ADS-B and AIS are now receive only in the dashboard.
- A phone connected to two federated servers no longer causes its Data Sync items to be removed and added back on the TAK Server.
- The Federation card now tells a TAK Server admin to add a group to the new federate, and explains which feeds cross.

**Tested with real phones, against TAK Server 5.8**
- Both directions: server connection, positions, new feeds, data packages, markers added to a feed, and feed deletes.
- TAK Server to PortableTAK: files added to a feed and items removed from a feed.

**Good to know**
- Tap refresh in Data Sync before relying on the feed list. ATAK's list can go stale.
- When a feed is deleted, TAK Server also sends an archive of it, which appears as a data package named after the feed.

**Build:** 2026.10.30.3. Fresh-drive build test: 7 of 7 services.

**Not yet tested:** the aircraft fix on a Windows computer, a larger TAK Server network with several servers, Raspberry Pi hosts, and the fix for items being removed and added back on a live server (it passed its automated test).
