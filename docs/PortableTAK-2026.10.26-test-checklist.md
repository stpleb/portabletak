# PortableTAK 2026.10.26 – trip test checklist

**Setup:** make the extra Lexar with the new Mac Setup (name it e.g. **Charlie TAK**), plug it into Node01 (blue USB port). Node01 updates itself from it (about 5 minutes extra), then the server starts. Dashboard: `https://10.41.250.10:8444` on Node01's Wi-Fi.

## 1. Live Map (dashboard header: Live Map)
- [ ] The S22 shows on the map (team colour), with its callsign.
- [ ] Tap it: details (updated, position, battery).
- [ ] With internet on Node01: map choice **Streets** and **Satellite** work.
- [ ] **Offline maps** > name an area > **Download this area**. Turn internet off (or open the map from a phone with no data): **Auto map** switches to the offline map.
- [ ] Drop a marker in ATAK: it appears on the map; delete it in ATAK: it disappears.

## 2. Live Feeds (dashboard card)
- [ ] Set the area (Use my location), tick **Aircraft from the internet**, Save. Aircraft appear on the Live Map within ~20 s.
- [ ] Channels card: give the S22's user **Receive** on **ADS-B**. Aircraft appear in ATAK after it reconnects.
- [ ] Optional: create a free key at aisstream.io, tick **Ships from the internet**, paste the key, Save (ships only near water).

## 3. ATAK Plugins (dashboard card)
- [ ] Upload `OpenManet-FieldExtension-1.1-ATAK-5.7.0-debug.apk` (it is in Field Ext/phone-install).
- [ ] On the S22: ATAK > Plugins > ⋮ > **Sync**: ManetNode FE is listed and installs.

## 4. Video (Video Feeds card)
- [ ] Add a feed (e.g. `test1`), push video to it (Skydio, or any RTSP app on a phone), press **Watch**.

## 5. Meshtastic (dashboard card)
- [ ] **Turn Meshtastic on** > **Apply now** (restarts the server, ~2 min).
- [ ] In the Meshtastic app for a radio: Settings > Modules > MQTT – enter the settings the card shows (server 10.41.250.10:1883, user, password, root topic `opentakserver`, encryption on, JSON off). Channels > LongFast > MQTT: Uplink and Downlink on. Radio without Wi-Fi: also turn on **MQTT client proxy** (the phone relays).
- [ ] The radio shows on the Live Map (kind: Meshtastic node).
- [ ] Channels card: S22's user **Receive** on **Meshtastic**: the radio shows in ATAK.
- [ ] Send a Meshtastic text message: it arrives in ATAK chat (and the other way).

Tell me what works and what doesn't (screenshots help); everything is logged on the drive.
