"""PortableTAK live feeds: ADS-B aircraft (internet: adsb.lol, free, ODbL; and/or a USB radio) and AIS ships
(internet: aisstream.io or AISHub), sent into OpenTAKServer as CoT the way its own ADS-B job does: into the
cot_parser (stored, on the live map), the feed's channel ("ADS-B" / "AIS", for phones
in that channel) and the firehose (federation).

Runs inside the cartridge chroot with OpenTAKServer's Python. Settings live in
/asgard/opentak/feeds.json (written by the dashboard) and are re-read every cycle, so
changes apply within seconds. Status goes to /asgard/opentak/feeds-status.json.

    python feeds_service.py            (started by /usr/local/portabletak/bin/portabletak-feeds)
"""
import datetime
import json
import os
import time
import traceback
import urllib.request
from xml.sax.saxutils import escape, quoteattr

CONFIG = os.environ.get('PORTABLETAK_FEEDS_CONFIG', '/asgard/opentak/feeds.json')
STATUS = os.environ.get('PORTABLETAK_FEEDS_STATUS', '/asgard/opentak/feeds-status.json')
RADIO_JSON = os.environ.get('PORTABLETAK_ADSB_RADIO_JSON', '/asgard/opentak/adsb/aircraft.json')
OTS_CONFIG = '/asgard/opentak/data/config.yml'
USER_AGENT = 'PortableTAK feeds'

DEFAULTS = {
    'adsb': {'internet': False, 'radio': False, 'lat': None, 'lon': None, 'radius_nm': 50,
             'api_url': 'https://api.adsb.lol/v2/point', 'interval': 15, 'group': 'ADS-B'},
    'ais': {'internet': False, 'aishub_username': '', 'aisstream_key': '', 'bbox': None, 'interval': 60, 'group': 'AIS'},
}
FT_TO_M = 0.3048
KN_TO_MS = 0.514444


def load_config(path=CONFIG):
    try:
        with open(path) as stream:
            data = json.load(stream)
    except (OSError, ValueError):
        data = {}
    out = {}
    for feed, defaults in DEFAULTS.items():
        merged = dict(defaults)
        merged.update({k: v for k, v in (data.get(feed) or {}).items() if k in defaults})
        out[feed] = merged
    return out


def _time(moment):
    return moment.strftime('%Y-%m-%dT%H:%M:%S.%fZ')[:-4] + 'Z'


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None   # not NaN


def aircraft_to_cot(craft, now=None, stale_seconds=90, source='ADS-B'):
    """CoT for one aircraft in readsb / airplanes.live (v2) JSON, or None without a position."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lat, lon = _number(craft.get('lat')), _number(craft.get('lon'))
    hexid = str(craft.get('hex') or '').strip().lower().lstrip('~')
    if lat is None or lon is None or not hexid:
        return None
    military = bool(int(craft.get('dbFlags') or 0) & 1)
    rotor = str(craft.get('category') or '').upper() == 'A7'
    cot_type = 'a-n-A-%s-%s' % ('M' if military else 'C', 'H' if rotor else 'F')
    callsign = (str(craft.get('flight') or '').strip() or str(craft.get('r') or '').strip() or hexid.upper())
    altitude = craft.get('alt_geom', craft.get('alt_baro'))
    on_ground = altitude == 'ground'
    hae = 0.0 if on_ground else (_number(altitude) or 0.0) * FT_TO_M
    course, speed = _number(craft.get('track')), _number(craft.get('gs'))
    remarks = ['%s aircraft' % source]
    for label, key in (('Type', 't'), ('Reg', 'r'), ('Squawk', 'squawk')):
        if craft.get(key):
            remarks.append('%s %s' % (label, craft[key]))
    if on_ground:
        remarks.append('on the ground')
    elif _number(craft.get('alt_baro')) is not None:
        remarks.append('%d ft' % _number(craft.get('alt_baro')))
    track = ''
    if course is not None or speed is not None:
        track = '<track course="%.1f" speed="%.2f"/>' % (course or 0.0, (speed or 0.0) * KN_TO_MS)
    stale = now + datetime.timedelta(seconds=stale_seconds)
    return ('<event version="2.0" uid=%s type="%s" how="m-g" time="%s" start="%s" stale="%s">'
            '<point lat="%.6f" lon="%.6f" hae="%.1f" ce="9999999.0" le="9999999.0"/>'
            '<detail><contact callsign=%s/>%s<remarks>%s</remarks></detail></event>'
            % (quoteattr('ICAO-' + hexid.upper()), cot_type, _time(now), _time(now), _time(stale),
               lat, lon, hae, quoteattr(callsign), track, escape(' · '.join(remarks))))


def vessel_to_cot(vessel, now=None, stale_seconds=600):
    """CoT for one AISHub vessel record (format=1, human readable), or None."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    lat, lon = _number(vessel.get('LATITUDE')), _number(vessel.get('LONGITUDE'))
    mmsi = str(vessel.get('MMSI') or '').strip()
    if lat is None or lon is None or not mmsi or abs(lat) > 90 or abs(lon) > 180:
        return None
    name = str(vessel.get('NAME') or '').strip() or 'MMSI ' + mmsi
    course, speed = _number(vessel.get('COG')), _number(vessel.get('SOG'))
    remarks = ['AIS vessel', 'MMSI ' + mmsi]
    for label, key in (('Callsign', 'CALLSIGN'), ('Destination', 'DEST')):
        if vessel.get(key):
            remarks.append('%s %s' % (label, str(vessel[key]).strip()))
    track = ''
    if course is not None and course < 360:
        track = '<track course="%.1f" speed="%.2f"/>' % (course, (speed or 0.0) * KN_TO_MS)
    stale = now + datetime.timedelta(seconds=stale_seconds)
    return ('<event version="2.0" uid=%s type="a-n-S-X" how="m-g" time="%s" start="%s" stale="%s">'
            '<point lat="%.6f" lon="%.6f" hae="0.0" ce="9999999.0" le="9999999.0"/>'
            '<detail><contact callsign=%s/>%s<remarks>%s</remarks></detail></event>'
            % (quoteattr('MMSI-' + mmsi), _time(now), _time(now), _time(stale), lat, lon,
               quoteattr(name), track, escape(' · '.join(remarks))))


def _get_json(url, timeout=20):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.load(response)


# Free community ADS-B sources (same aircraft fields); the next one is used when one
# refuses (e.g. adsb.lol rate-limits busy shared networks such as hotels).
FALLBACK_URLS = ['https://opendata.adsb.fi/api/v2/lat/{lat}/lon/{lon}/dist/{nm}']


def fetch_internet_aircraft(cfg):
    if cfg['lat'] is None or cfg['lon'] is None:
        raise ValueError('set_a_center_point')
    radius = max(1, min(250, int(float(cfg['radius_nm']))))
    lat, lon = '%.4f' % float(cfg['lat']), '%.4f' % float(cfg['lon'])
    urls = ['%s/%s/%s/%d' % (cfg['api_url'].rstrip('/'), lat, lon, radius)]
    urls += [u.format(lat=lat, lon=lon, nm=radius) for u in FALLBACK_URLS]
    error = None
    for url in urls:
        try:
            data = _get_json(url)
            return data.get('ac') or data.get('aircraft') or []   # adsb.lol: ac, adsb.fi: aircraft
        except Exception as failure:   # try the next source
            error = failure
    raise error


def read_radio_aircraft(path=RADIO_JSON, max_age=60):
    """Aircraft from a local readsb (USB radio); empty when the radio is not running."""
    try:
        if time.time() - os.path.getmtime(path) > max_age:
            raise ValueError('radio_not_updating')
        with open(path) as stream:
            data = json.load(stream)
    except FileNotFoundError:
        raise ValueError('no_radio_data')
    return [a for a in data.get('aircraft') or [] if a.get('seen_pos', 0) <= 60]


def fetch_ais(cfg):
    if not cfg['aishub_username']:
        raise ValueError('set_an_ais_source')
    params = 'username=%s&format=1&output=json&compress=0' % urllib.request.quote(cfg['aishub_username'])
    if cfg.get('bbox'):
        west, south, east, north = cfg['bbox']
        params += '&latmin=%s&latmax=%s&lonmin=%s&lonmax=%s' % (south, north, west, east)
    data = _get_json('https://data.aishub.net/ws.php?' + params, timeout=30)
    if isinstance(data, list) and data and isinstance(data[0], dict) and data[0].get('ERROR'):
        raise ValueError('aishub: ' + str(data[0].get('ERROR_MESSAGE') or 'error'))
    return data[1] if isinstance(data, list) and len(data) > 1 else []


class AisStream:
    """aisstream.io: a WebSocket that streams AIS for a bounding box (free API key).
    A thread keeps the newest report per ship; the service publishes them each cycle."""
    URL = 'wss://stream.aisstream.io/v0/stream'
    TYPES = ['PositionReport', 'StandardClassBPositionReport', 'ExtendedClassBPositionReport', 'ShipStaticData']

    def __init__(self, key, bbox):
        import threading
        self.key, self.bbox = key, bbox
        self.vessels, self.error, self.stop = {}, None, threading.Event()
        self.lock = threading.Lock()
        self.thread = threading.Thread(target=self._run, name='aisstream', daemon=True)
        self.thread.start()

    def _subscribe(self):
        west, south, east, north = self.bbox or (-180, -90, 180, 90)
        return json.dumps({'APIKey': self.key, 'BoundingBoxes': [[[south, west], [north, east]]],
                           'FilterMessageTypes': self.TYPES})

    def _run(self):
        import websocket
        delay = 5
        while not self.stop.is_set():
            try:
                ws = websocket.create_connection(self.URL, timeout=60, header=['User-Agent: ' + USER_AGENT])
                ws.send(self._subscribe())
                self.error, delay = None, 5
                while not self.stop.is_set():
                    raw = ws.recv()
                    if raw:
                        self.handle(json.loads(raw))
            except Exception as error:   # reconnect with backoff
                self.error = str(error)[:200] or type(error).__name__
                self.stop.wait(delay)
                delay = min(delay * 2, 300)

    def handle(self, message):
        if message.get('error'):
            self.error = str(message['error'])[:200]
            return
        meta = message.get('MetaData') or {}
        mmsi = str(meta.get('MMSI') or '').strip()
        if not mmsi:
            return
        body = (message.get('Message') or {}).get(message.get('MessageType'), {}) or {}
        with self.lock:
            vessel = self.vessels.setdefault(mmsi, {'MMSI': mmsi})
            vessel['seen'] = time.time()
            if meta.get('ShipName', '').strip():
                vessel['NAME'] = meta['ShipName'].strip()
            if message.get('MessageType') == 'ShipStaticData':
                vessel['CALLSIGN'] = str(body.get('CallSign') or '').strip()
                vessel['DEST'] = str(body.get('Destination') or '').strip()
                if str(body.get('Name') or '').strip():
                    vessel['NAME'] = body['Name'].strip()
            else:
                vessel['LATITUDE'] = body.get('Latitude', meta.get('latitude'))
                vessel['LONGITUDE'] = body.get('Longitude', meta.get('longitude'))
                vessel['COG'], vessel['SOG'] = body.get('Cog'), body.get('Sog')

    def current(self, max_age=900):
        cutoff = time.time() - max_age
        with self.lock:
            for mmsi in [m for m, v in self.vessels.items() if v['seen'] < cutoff]:
                del self.vessels[mmsi]
            return [dict(v) for v in self.vessels.values() if 'LATITUDE' in v]

    def close(self):
        self.stop.set()


class Publisher:
    """One RabbitMQ connection, reopened when the broker drops it."""

    def __init__(self):
        import yaml
        with open(OTS_CONFIG) as stream:
            self.cfg = yaml.safe_load(stream) or {}
        self.connection = self.channel = None
        self.declared = set()

    def _open(self):
        import pika
        credentials = pika.PlainCredentials(self.cfg.get('OTS_RABBITMQ_USERNAME', 'guest'),
                                            self.cfg.get('OTS_RABBITMQ_PASSWORD', 'guest'))
        self.connection = pika.BlockingConnection(pika.ConnectionParameters(
            host=self.cfg.get('OTS_RABBITMQ_SERVER_ADDRESS', '127.0.0.2'), credentials=credentials))
        self.channel = self.connection.channel()
        self.declared = set()

    def publish(self, events, group):
        import pika
        for attempt in (1, 2):
            try:
                if self.connection is None or self.connection.is_closed:
                    self._open()
                if group not in self.declared:
                    self.channel.exchange_declare(exchange=group, exchange_type='fanout')
                    self.declared.add(group)
                props = pika.BasicProperties(expiration=self.cfg.get('OTS_RABBITMQ_TTL'))
                for cot in events:
                    body = json.dumps({'cot': cot, 'uid': self.cfg.get('OTS_NODE_ID')})
                    self.channel.basic_publish(exchange='cot_parser', routing_key='cot_parser', body=body, properties=props)
                    self.channel.basic_publish(exchange='groups', routing_key=group + '.OUT', body=body, properties=props)
                    self.channel.basic_publish(exchange='firehose', routing_key='', body=body, properties=props)
                self.connection.process_data_events(0)   # keep heartbeats going between cycles
                return
            except (pika.exceptions.AMQPError, OSError):
                self.connection = None
                if attempt == 2:
                    raise


def write_status(status, path=STATUS):
    tmp = path + '.tmp'
    with open(tmp, 'w') as stream:
        json.dump(status, stream)
    os.replace(tmp, path)


def run_once(cfg, publisher, status, due, now_ts, streams=None):
    """One pass over the feeds whose interval has elapsed."""
    adsb, ais = cfg['adsb'], cfg['ais']
    streams = {} if streams is None else streams
    if (adsb['internet'] or adsb['radio']) and now_ts >= due.get('adsb', 0):
        due['adsb'] = now_ts + (5 if adsb['radio'] and not adsb['internet'] else max(5, int(adsb['interval'])))
        crafts, errors, sources = {}, [], []
        if adsb['internet'] and now_ts >= due.get('adsb_backoff', 0):
            try:
                for craft in fetch_internet_aircraft(adsb):
                    crafts[str(craft.get('hex'))] = (craft, 'ADS-B (internet)')
                sources.append('internet')
                due.pop('adsb_strikes', None)
            except Exception as error:
                errors.append('internet: %s' % error)
                if '429' in str(error):   # rate limited: wait 1, 2, 4 ... up to 10 minutes
                    due['adsb_strikes'] = due.get('adsb_strikes', 0) + 1
                    due['adsb_backoff'] = now_ts + min(600, 30 * 2 ** (due['adsb_strikes'] - 1))
        elif adsb['internet']:
            errors.append('internet: rate limited, waiting %d s' % int(due['adsb_backoff'] - now_ts))
        if adsb['radio']:
            try:
                for craft in read_radio_aircraft():   # the radio wins: newer and local
                    crafts[str(craft.get('hex'))] = (craft, 'ADS-B (radio)')
                sources.append('radio')
            except Exception as error:
                errors.append('radio: %s' % error)
        stale = 3 * (due['adsb'] - now_ts) + 30
        events = [e for e in (aircraft_to_cot(c, stale_seconds=stale, source=s) for c, s in crafts.values()) if e]
        if events:
            publisher.publish(events, adsb['group'])
        status['adsb'] = {'time': int(now_ts), 'count': len(events), 'sources': sources, 'error': '; '.join(errors) or None}
    elif not (adsb['internet'] or adsb['radio']):
        status['adsb'] = {'off': True}
    stream = streams.get('ais')
    wanted_stream = (ais['aisstream_key'], tuple(ais['bbox'] or ())) if ais['internet'] and ais['aisstream_key'] else None
    if stream and (wanted_stream is None or (stream.key, tuple(stream.bbox or ())) != wanted_stream):
        stream.close()
        streams.pop('ais')
        stream = None
    if wanted_stream and stream is None:
        stream = streams['ais'] = AisStream(ais['aisstream_key'], ais['bbox'])
    if ais['internet'] and now_ts >= due.get('ais', 0):
        # aisstream.io: publish what the stream gathered; AISHub allows one request a minute.
        due['ais'] = now_ts + (30 if stream else max(60, int(ais['interval'])))
        try:
            vessels = stream.current() if stream else fetch_ais(ais)
            if stream and stream.error and not vessels:
                raise ValueError('aisstream: ' + stream.error)
            events = [e for e in (vessel_to_cot(v) for v in vessels) if e]
            if events:
                publisher.publish(events, ais['group'])
            status['ais'] = {'time': int(now_ts), 'count': len(events), 'error': None}
        except Exception as error:
            status['ais'] = {'time': int(now_ts), 'count': 0, 'error': str(error)[:200]}
    elif not ais['internet']:
        status['ais'] = {'off': True}


def main():
    publisher, status, due, streams = None, {}, {}, {}
    while True:
        try:
            cfg = load_config()
            if publisher is None:
                publisher = Publisher()
            run_once(cfg, publisher, status, due, time.time(), streams)
            write_status(status)
        except Exception:
            traceback.print_exc()
            time.sleep(10)
        time.sleep(2)


if __name__ == '__main__':
    main()
