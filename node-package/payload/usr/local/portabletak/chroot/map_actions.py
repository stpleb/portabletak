"""Live map data for the PortableTAK dashboard, run inside the OpenTAKServer app
context by users_worker.

One snapshot holds everything currently on the map: phones (local and federated),
ADS-B aircraft, AIS ships, Meshtastic nodes, markers and other CoT items that carry a
position and are not stale. It is built from the cot table (the newest event per uid),
so anything OpenTAKServer stores shows up without per-type code. Markers deleted in
ATAK (t-x-d-d with a link to the marker) are left out.
"""
import xml.etree.ElementTree as ET

MAX_ITEMS = 3000

# Newest event per uid that is still current and has a position. Chat, pings and other
# t-/b-t- control traffic carry no map item.
LATEST = """
    SELECT DISTINCT ON (uid) uid, type, how, xml, stale, timestamp, sender_uid
    FROM cot
    WHERE stale > (now() AT TIME ZONE 'utc')
      AND type NOT LIKE 't-%' AND type NOT LIKE 'b-t-%' AND type NOT LIKE 'y-%'
    ORDER BY uid, id DESC
"""
DELETES = """
    SELECT xml, timestamp FROM cot
    WHERE type = 't-x-d-d' AND timestamp > (now() AT TIME ZONE 'utc') - interval '2 days'
"""
EUDS = "SELECT uid, callsign, last_status, platform FROM euds"
# OpenTAKServer keeps Meshtastic radio positions in points (it sends no CoT to the
# cot table for them). Radios report every 15 min or so: show those heard in 2 hours.
MESH = """
    SELECT DISTINCT ON (p.device_uid) p.device_uid, p.latitude, p.longitude, p.hae, p.course, p.speed,
           p.battery, p.timestamp, e.callsign, e.device
    FROM points p JOIN euds e ON e.uid = p.device_uid
    WHERE e.platform = 'Meshtastic' AND p.timestamp > (now() AT TIME ZONE 'utc') - interval '2 hours'
    ORDER BY p.device_uid, p.id DESC
"""

AFFILIATION = {'f': 'friend', 'a': 'friend', 'h': 'hostile', 's': 'hostile', 'j': 'hostile',
               'k': 'hostile', 'n': 'neutral', 'u': 'unknown', 'p': 'unknown', 'o': 'unknown'}


def _float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _kind(uid, cot_type, eud):
    if eud is not None:
        status = eud.get('last_status') or ''
        return 'federated' if status.startswith('Federated') else 'phone'
    if uid.startswith('ICAO-') or (cot_type.startswith('a-') and cot_type[4:5] == 'A'):
        return 'aircraft'
    if uid.startswith('MMSI-') or (cot_type.startswith('a-') and cot_type[4:5] == 'S'):
        return 'ship'
    if uid.startswith('!') or 'meshtastic' in uid.lower():
        return 'meshtastic'
    if cot_type.startswith('b-r-f-h-c'):
        return 'casevac'
    if cot_type.startswith('u-rb-a'):
        return 'range_line'
    if cot_type.startswith('b-a-'):
        return 'alert'
    if cot_type.startswith('a-'):
        return 'unit'
    return 'marker'


def item(row, euds):
    """One map item from a cot row, or None when the event has no usable position."""
    uid, cot_type, how, xml, stale, timestamp, sender_uid = row
    try:
        event = ET.fromstring(xml)
    except ET.ParseError:
        return None
    point = event.find('point')
    if point is None:
        return None
    lat, lon = _float(point.get('lat')), _float(point.get('lon'))
    if lat is None or lon is None or (lat == 0 and lon == 0) or abs(lat) > 90 or abs(lon) > 180:
        return None
    detail = event.find('detail')
    found = (lambda tag: detail.find(tag) if detail is not None else None)
    contact, group, track = found('contact'), found('__group'), found('track')
    remarks, color, icon = found('remarks'), found('color'), found('usericon')
    status, link = found('status'), found('link')
    eud = euds.get(uid)
    out = {
        'uid': uid,
        'type': cot_type or '',
        'kind': _kind(uid, cot_type or '', eud),
        'callsign': (contact.get('callsign') if contact is not None else None)
                    or (eud or {}).get('callsign') or uid,
        'lat': lat, 'lon': lon,
        'hae': _float(point.get('hae')),
        'ce': _float(point.get('ce')),
        'how': how or '',
        'time': timestamp.isoformat() + 'Z' if timestamp else None,
        'stale': stale.isoformat() + 'Z' if stale else None,
        'affiliation': AFFILIATION.get((cot_type or '')[2:3], 'unknown') if (cot_type or '').startswith('a-') else None,
    }
    if group is not None:
        out['team'], out['role'] = group.get('name'), group.get('role')
    if track is not None:
        out['course'], out['speed'] = _float(track.get('course')), _float(track.get('speed'))
    if remarks is not None and (remarks.text or '').strip():
        out['remarks'] = remarks.text.strip()[:500]
    if color is not None and color.get('argb'):
        out['argb'] = color.get('argb')
    if icon is not None and icon.get('iconsetpath'):
        out['icon'] = icon.get('iconsetpath')
    if status is not None and status.get('battery'):
        out['battery'] = _float(status.get('battery'))
    if link is not None and link.get('parent_callsign'):
        out['parent'] = link.get('parent_callsign')
    if eud is not None:
        out['status'] = eud.get('last_status')
        out['platform'] = eud.get('platform')
    return out


def deleted_uids(rows):
    """uid -> time of the newest ATAK delete (t-x-d-d linking the deleted item)."""
    deleted = {}
    for xml, timestamp in rows:
        try:
            event = ET.fromstring(xml)
        except ET.ParseError:
            continue
        for link in event.iter('link'):
            target = link.get('uid')
            if target and (target not in deleted or timestamp > deleted[target]):
                deleted[target] = timestamp
    return deleted


def mesh_item(row):
    uid, lat, lon, hae, course, speed, battery, timestamp, callsign, device = row
    lat, lon = _float(lat), _float(lon)
    if lat is None or lon is None or (lat == 0 and lon == 0):
        return None
    out = {'uid': uid, 'type': 'a-f-G-U-C', 'kind': 'meshtastic', 'callsign': callsign or uid,
           'lat': lat, 'lon': lon, 'hae': _float(hae), 'how': 'm-g', 'affiliation': 'friend',
           'time': timestamp.isoformat() + 'Z' if timestamp else None, 'platform': 'Meshtastic'}
    if _float(course) is not None and _float(speed):
        out['course'], out['speed'] = _float(course), _float(speed)
    if _float(battery):
        out['battery'] = _float(battery)
    if device and device != 'UNSET':
        out['remarks'] = 'Meshtastic ' + str(device)
    return out


def snapshot(db):
    from sqlalchemy import text
    euds = {r[0]: {'callsign': r[1], 'last_status': r[2], 'platform': r[3]}
            for r in db.session.execute(text(EUDS))}
    deleted = deleted_uids(db.session.execute(text(DELETES)))
    items = []
    for row in db.session.execute(text(LATEST)):
        uid, timestamp = row[0], row[5]
        if uid in deleted and timestamp is not None and deleted[uid] >= timestamp:
            continue
        entry = item(row, euds)
        if entry is not None:
            items.append(entry)
        if len(items) >= MAX_ITEMS:
            break
    seen = {i['uid'] for i in items}
    for row in db.session.execute(text(MESH)):
        entry = mesh_item(row)
        if entry is not None and entry['uid'] not in seen and len(items) < MAX_ITEMS:
            items.append(entry)
    return items


def handle(app, db, request):
    action = request.get('action', '')
    if action == 'map_snapshot':
        return {'ok': True, 'items': snapshot(db)}
    return {'ok': False, 'error': 'invalid_action'}
