#!/usr/bin/env python3
"""Voice history for PortableTAK: records every transmission on the voice server (Murmur).

It signs in to Murmur as a listen-only user ("PortableTAK Recorder", its own certificate, which
takptt_auth.py lets in by fingerprint), listens to every channel (Mumble 1.4 channel listeners)
and saves each transmission as its own Ogg Opus clip, straight from the Opus packets (no
re-encoding). SQLite keeps who sent it, on which channel, when, how long and where from: TAK PTT
sends its position to the recorder in a private message ("takptt-pos lat,lon") when you key up.

A small HTTP API on a Unix socket (--socket; only nginx can reach it, not the network or
Tailscale) serves the clips; nginx puts it at https://<server>:8443/voice/ and passes on the
phone's TAK certificate, which is checked again here against the CA (--ca) and must belong to
an active PortableTAK account. Clips older than --days (7) are deleted, the oldest go first when
the clips pass --max-mb, a clip is cut at 2 minutes, and nothing is recorded while the drive has
less than 1 GB free.

The API also hands out the mesh keys (GET /keys): one AES key per channel and week, made from a
secret kept in the voice data folder, so TAK PTT can keep talking over the local network
(encrypted UDP multicast) when this server cannot be reached. The keys change every week, so a
phone that is no longer allowed on the server loses the mesh within a week.

    takptt_recorder.py --data /asgard/opentak/voice --ca ca.pem [--socket api.sock]
                       [--host 127.0.0.1] [--port 64738] [--ids ids.json] [--db-config config.yml]
                       [--days 7] [--max-mb 2000]

Only the Python standard library (and, for the account check, OpenTAKServer's own psycopg,
PyYAML and cryptography) is used; the Mumble protocol is spoken directly.
"""
import argparse
import json
import logging
import os
import socket
import sqlite3
import ssl
import struct
import subprocess
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

log = logging.getLogger('takptt-recorder')

NAME = 'PortableTAK Recorder'
VERSION = (1 << 16) | (4 << 8) | 0       # Mumble 1.4.0: channel listeners, classic audio packets
GAP = 0.6                                 # seconds of silence that end a transmission
MAX_CLIP = 120                            # seconds: a longer transmission is cut into clips
MIN_FREE = 1024 ** 3                      # bytes free on the drive needed to record
POSITION_AGE = 30                         # a position this recent is used for a clip

# ------------------------------------------------------------------ protobuf, by hand
# Only the few Mumble messages and fields the recorder needs.

def _varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def pb(fields):
    """Encode [(field number, value)]: int -> varint, bool -> varint, str/bytes -> bytes."""
    out = bytearray()
    for num, val in fields:
        if isinstance(val, (bool, int)):
            out += _varint(num << 3) + _varint(int(val))
        else:
            data = val.encode() if isinstance(val, str) else val
            out += _varint((num << 3) | 2) + _varint(len(data)) + data
    return bytes(out)


def unpb(data):
    """Decode to {field: [values]} (varints as int, length-delimited as bytes)."""
    out, i = {}, 0
    while i < len(data):
        key, i = _read_varint(data, i)
        num, wt = key >> 3, key & 7
        if wt == 0:
            val, i = _read_varint(data, i)
        elif wt == 2:
            n, i = _read_varint(data, i)
            val, i = data[i:i + n], i + n
        elif wt == 1:
            val, i = data[i:i + 8], i + 8
        elif wt == 5:
            val, i = data[i:i + 4], i + 4
        else:
            raise ValueError('wire type %d' % wt)
        out.setdefault(num, []).append(val)
    return out


def _read_varint(data, i):
    n = shift = 0
    while True:
        b = data[i]
        i += 1
        n |= (b & 0x7F) << shift
        shift += 7
        if not b & 0x80:
            return n, i


def mumble_varint(data, i):
    """Mumble's own variable-length integer (used in voice packets)."""
    b = data[i]
    if b & 0x80 == 0:
        return b, i + 1
    if b & 0xC0 == 0x80:
        return ((b & 0x3F) << 8) | data[i + 1], i + 2
    if b & 0xE0 == 0xC0:
        return ((b & 0x1F) << 16) | (data[i + 1] << 8) | data[i + 2], i + 3
    if b & 0xF0 == 0xE0:
        return ((b & 0x0F) << 24) | (data[i + 1] << 16) | (data[i + 2] << 8) | data[i + 3], i + 4
    if b & 0xFC == 0xF0:
        return struct.unpack('>I', data[i + 1:i + 5])[0], i + 5
    if b & 0xFC == 0xF4:
        return struct.unpack('>Q', data[i + 1:i + 9])[0], i + 9
    if b & 0xFC == 0xF8:
        n, j = mumble_varint(data, i + 1)
        return -n, j
    return ~(b & 0x03), i + 1


# TCP message types
VERSION_MSG, UDPTUNNEL, AUTHENTICATE, PING, REJECT, SERVERSYNC = 0, 1, 2, 3, 4, 5
CHANNELREMOVE, CHANNELSTATE, USERREMOVE, USERSTATE, TEXTMESSAGE = 6, 7, 8, 9, 11

# ------------------------------------------------------------------ Ogg Opus files


def _crc_table():
    t = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) if r & 0x80000000 else (r << 1)
        t.append(r & 0xFFFFFFFF)
    return t


_CRC = _crc_table()


def ogg_crc(data):
    c = 0
    for b in data:
        c = ((c << 8) & 0xFFFFFFFF) ^ _CRC[((c >> 24) & 0xFF) ^ b]
    return c


def opus_samples(packet):
    """Samples (at 48 kHz) in one Opus packet, from its TOC byte."""
    if not packet:
        return 0
    toc = packet[0]
    config = toc >> 3
    if config < 12:
        size = (480, 960, 1920, 2880)[config % 4]
    elif config < 16:
        size = (480, 960)[config % 2]
    else:
        size = (120, 240, 480, 960)[config % 4]
    count = toc & 3
    frames = 1 if count == 0 else 2 if count in (1, 2) else (packet[1] & 0x3F if len(packet) > 1 else 1)
    return size * frames


class OggOpus:
    """Writes one transmission as an Ogg Opus file (mono, 48 kHz), one packet per page."""

    def __init__(self, path):
        self.f = open(path, 'wb')
        self.serial = int.from_bytes(os.urandom(4), 'little')
        self.seq = 0
        self.granule = 0
        self._page(b'OpusHead' + struct.pack('<BBHIhB', 1, 1, 312, 48000, 0, 0), 0, 0x02)
        vendor = b'TAK PTT recorder'
        self._page(b'OpusTags' + struct.pack('<I', len(vendor)) + vendor + struct.pack('<I', 0), 0, 0)

    def _page(self, packet, granule, flags):
        lacing = bytearray()
        n = len(packet)
        while n >= 255:
            lacing.append(255)
            n -= 255
        lacing.append(n)
        head = struct.pack('<4sBBqIIIB', b'OggS', 0, flags, granule, self.serial, self.seq, 0, len(lacing))
        page = bytearray(head + bytes(lacing) + packet)
        struct.pack_into('<I', page, 22, ogg_crc(page))
        self.f.write(page)
        self.seq += 1

    def add(self, packet):
        self.granule += opus_samples(packet)
        self._page(packet, self.granule, 0)

    def close(self):
        self._page(b'', self.granule, 0x04)        # an empty last page marks the end
        self.f.close()
        return self.granule / 48000.0


# ------------------------------------------------------------------ the clip store


class Store:
    def __init__(self, data, days, max_mb):
        self.dir = os.path.join(data, 'clips')
        os.makedirs(self.dir, exist_ok=True)
        self.days, self.max_mb = days, max_mb
        self.lock = threading.Lock()
        self.db = sqlite3.connect(os.path.join(data, 'history.sqlite'), check_same_thread=False)
        self.db.execute('''CREATE TABLE IF NOT EXISTS clips (
            id INTEGER PRIMARY KEY, file TEXT, channel_id INTEGER, channel TEXT, sender TEXT,
            tak_user TEXT, start REAL, duration REAL, lat REAL, lon REAL, bytes INTEGER)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS clips_start ON clips(start)')
        self.db.commit()

    def path(self, start):
        day = time.strftime('%Y-%m-%d', time.gmtime(start))
        os.makedirs(os.path.join(self.dir, day), exist_ok=True)
        return os.path.join(self.dir, day, '%d-%s.opus' % (int(start * 1000), os.urandom(3).hex()))

    def add(self, file, channel_id, channel, sender, tak_user, start, duration, lat, lon):
        with self.lock:
            cur = self.db.execute(
                'INSERT INTO clips (file, channel_id, channel, sender, tak_user, start, duration, lat, lon, bytes) '
                'VALUES (?,?,?,?,?,?,?,?,?,?)',
                (file, channel_id, channel, sender, tak_user, start, duration, lat, lon, os.path.getsize(file)))
            self.db.commit()
            cid = cur.lastrowid
        self.prune()                                # keep under the size cap at all times
        return cid

    def room(self):
        """True while the drive has room to record (it is shared with the TAK server)."""
        import shutil
        return shutil.disk_usage(self.dir).free > MIN_FREE

    def list(self, since=0, limit=300):
        with self.lock:
            rows = self.db.execute(
                'SELECT id, channel, sender, tak_user, start, duration, lat, lon FROM clips '
                'WHERE start > ? ORDER BY start DESC LIMIT ?', (since, limit)).fetchall()
        keys = ('id', 'channel', 'sender', 'user', 'start', 'duration', 'lat', 'lon')
        return [dict(zip(keys, r)) for r in rows]

    def file(self, clip_id):
        with self.lock:
            row = self.db.execute('SELECT file FROM clips WHERE id = ?', (clip_id,)).fetchone()
        return row[0] if row else None

    def prune(self):
        """Drop clips past the retention time, then the oldest while over the size cap."""
        with self.lock:
            old = self.db.execute('SELECT id, file FROM clips WHERE start < ?',
                                  (time.time() - self.days * 86400,)).fetchall()
            total = self.db.execute('SELECT COALESCE(SUM(bytes), 0) FROM clips').fetchone()[0]
            over = []
            if total > self.max_mb * 1024 * 1024:
                for cid, f, size in self.db.execute('SELECT id, file, bytes FROM clips ORDER BY start'):
                    over.append((cid, f))
                    total -= size
                    if total <= self.max_mb * 1024 * 1024:
                        break
            for cid, f in old + over:
                try:
                    os.remove(f)
                except OSError:
                    pass
                self.db.execute('DELETE FROM clips WHERE id = ?', (cid,))
            self.db.commit()
        if old or over:
            log.info('pruned %d clips', len(old) + len(over))


# ------------------------------------------------------------------ the Mumble client


class Recorder:
    def __init__(self, args, store):
        self.args = args
        self.store = store
        self.sock = None
        self.send_lock = threading.Lock()
        self.session = None
        self.users = {}          # session -> {'name', 'user_id', 'channel'}
        self.channels = {}       # id -> name
        self.listening = set()
        self.talks = {}          # session -> {'ogg', 'file', 'start', 'last', 'channel'}
        self.positions = {}      # session -> (lat, lon, when)
        self.talk_lock = threading.Lock()

    # -- connection

    def identity(self):
        """This recorder's own certificate (made on first start); its fingerprint goes in
        recorder.sha1 so takptt_auth.py lets it in."""
        cert = os.path.join(self.args.data, 'recorder.pem')
        key = os.path.join(self.args.data, 'recorder.key')
        if not (os.path.exists(cert) and os.path.exists(key)):
            subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
                            '-subj', '/CN=' + NAME, '-keyout', key, '-out', cert],
                           check=True, capture_output=True)
            os.chmod(key, 0o600)
        der = subprocess.run(['openssl', 'x509', '-in', cert, '-outform', 'DER'], check=True,
                             capture_output=True).stdout
        import hashlib
        with open(os.path.join(self.args.data, 'recorder.sha1'), 'w') as f:
            f.write(hashlib.sha1(der).hexdigest() + '\n')
        return cert, key

    def send(self, kind, payload):
        with self.send_lock:
            self.sock.sendall(struct.pack('>HI', kind, len(payload)) + payload)

    def run_forever(self):
        while True:
            try:
                self.run_once()
            except Exception as e:
                log.warning('voice server connection: %s', e)
            self.end_all()
            time.sleep(5)

    def run_once(self):
        cert, key = self.identity()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE            # Murmur on this same machine (127.0.0.1)
        ctx.load_cert_chain(cert, key)
        raw = socket.create_connection((self.args.host, self.args.port), timeout=20)
        self.sock = ctx.wrap_socket(raw)
        self.sock.settimeout(None)
        self.session, self.users, self.channels, self.listening = None, {}, {}, set()
        self.send(VERSION_MSG, pb([(1, VERSION), (2, '1.4.0'), (3, 'PortableTAK'), (4, 'recorder')]))
        self.send(AUTHENTICATE, pb([(1, NAME), (5, True)]))
        threading.Thread(target=self.pinger, args=(self.sock,), daemon=True).start()
        buf = b''
        while True:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError('closed')
            buf += chunk
            while len(buf) >= 6:
                kind, size = struct.unpack('>HI', buf[:6])
                if len(buf) < 6 + size:
                    break
                payload, buf = buf[6:6 + size], buf[6 + size:]
                self.handle(kind, payload)

    def pinger(self, sock):
        while self.sock is sock:
            try:
                self.send(PING, pb([(1, int(time.time() * 1000))]))
            except Exception:
                return
            time.sleep(15)

    # -- messages

    def handle(self, kind, payload):
        if kind == UDPTUNNEL:
            self.voice(payload)
            return
        if kind in (VERSION_MSG, PING):
            return
        m = unpb(payload)
        if kind == REJECT:
            raise ConnectionError('refused: %s' % (m.get(2, [b''])[0].decode(errors='replace')))
        if kind == SERVERSYNC:
            self.session = m[1][0]
            log.info('signed in to voice as session %d', self.session)
            # Recording only: mute ourselves (never deafen, or the server sends no audio).
            self.send(USERSTATE, pb([(1, self.session), (9, True)]))
            self.listen_all()
        elif kind == CHANNELSTATE:
            cid = m.get(1, [0])[0]
            if 3 in m:
                self.channels[cid] = m[3][0].decode(errors='replace')
            else:
                self.channels.setdefault(cid, str(cid))
            if self.session is not None:
                self.listen_all()
        elif kind == CHANNELREMOVE:
            self.channels.pop(m.get(1, [0])[0], None)
            self.listening.discard(m.get(1, [0])[0])
        elif kind == USERSTATE:
            s = m.get(1, [None])[0]
            if s is None:
                return
            u = self.users.setdefault(s, {'name': '', 'user_id': -1, 'channel': 0})
            if 3 in m:
                u['name'] = m[3][0].decode(errors='replace')
            if 4 in m:
                u['user_id'] = m[4][0]
            if 5 in m:
                u['channel'] = m[5][0]
        elif kind == USERREMOVE:
            s = m.get(1, [None])[0]
            self.end_talk(s)
            self.users.pop(s, None)
        elif kind == TEXTMESSAGE:
            self.text(m)

    def listen_all(self):
        """Listen to every channel except the one we are in (we hear that one anyway)."""
        here = self.users.get(self.session, {}).get('channel', 0)
        want = {c for c in self.channels if c != here}
        add = want - self.listening
        if add:
            self.send(USERSTATE, pb([(1, self.session)] + [(21, c) for c in sorted(add)]))
            self.listening |= add
            log.info('listening to %d channels', len(self.listening))

    def text(self, m):
        """A private "takptt-pos lat,lon" from TAK PTT: where that user is as they key up."""
        actor = m.get(1, [None])[0]
        msg = m.get(5, [b''])[0].decode(errors='replace').strip()
        if actor is None or not msg.startswith('takptt-pos '):
            return
        try:
            lat, lon = (float(x) for x in msg.split(None, 1)[1].split(','))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                self.positions[actor] = (lat, lon, time.time())
                with self.talk_lock:
                    t = self.talks.get(actor)
                    if t and t.get('lat') is None:
                        t['lat'], t['lon'] = lat, lon
        except ValueError:
            pass

    # -- voice

    def voice(self, data):
        """A classic Mumble voice packet: header, session, sequence, Opus frame (with the
        end-of-transmission flag), optional position."""
        if not data:
            return
        kind = data[0] >> 5
        if kind != 4:                              # 4 = Opus; 1 = ping; others are old codecs
            return
        try:
            session, i = mumble_varint(data, 1)
            _seq, i = mumble_varint(data, i)
            header, i = mumble_varint(data, i)
            size, last = header & 0x1FFF, bool(header & 0x2000)
            frame = data[i:i + size]
        except (IndexError, struct.error):
            return
        if session == self.session:
            return
        now = time.time()
        with self.talk_lock:
            t = self.talks.get(session)
            if t is not None and now - t['start'] > MAX_CLIP:
                t = None                           # cut here; the rest goes in a new clip
                split = True
            else:
                split = False
        if split:
            self.end_talk(session)
        with self.talk_lock:
            t = self.talks.get(session)
            if t is None:
                if not self.store.room():
                    log.warning('drive nearly full: not recording')
                    return
                u = self.users.get(session, {})
                start = now
                path = self.store.path(start)
                pos = self.positions.get(session)
                lat = lon = None
                if pos and now - pos[2] < POSITION_AGE:
                    lat, lon = pos[0], pos[1]
                t = {'ogg': OggOpus(path), 'file': path, 'start': start, 'last': now,
                     'channel': u.get('channel', 0), 'name': u.get('name', '?'),
                     'user_id': u.get('user_id', -1), 'lat': lat, 'lon': lon}
                self.talks[session] = t
            if frame:
                t['ogg'].add(frame)
            t['last'] = now
        if last:
            self.end_talk(session)

    def end_talk(self, session):
        with self.talk_lock:
            t = self.talks.pop(session, None)
        if not t:
            return
        duration = t['ogg'].close()
        if duration < 0.25:                        # a click of the key: not worth keeping
            os.remove(t['file'])
            return
        channel = self.channels.get(t['channel'], str(t['channel']))
        if t['channel'] == 0:
            channel = 'LOBBY'
        cid = self.store.add(t['file'], t['channel'], channel, t['name'], self.tak_user(t['user_id']),
                             t['start'], duration, t['lat'], t['lon'])
        log.info('clip %d: %s on %s, %.1f s%s', cid, t['name'], channel, duration,
                 '' if t['lat'] is None else ' at %.5f,%.5f' % (t['lat'], t['lon']))

    def end_idle(self):
        """End transmissions that went quiet without the end flag (a lost packet)."""
        while True:
            time.sleep(0.2)
            now = time.time()
            with self.talk_lock:
                idle = [s for s, t in self.talks.items() if now - t['last'] > GAP]
            for s in idle:
                self.end_talk(s)

    def end_all(self):
        for s in list(self.talks):
            self.end_talk(s)

    def tak_user(self, user_id):
        """The TAK user name for a voice user id (takptt_auth.py's ids.json)."""
        if not self.args.ids or user_id is None or user_id < 0:
            return None
        try:
            with open(self.args.ids) as f:
                ids = json.load(f).get('ids', {})
            for name, uid in ids.items():
                if uid == user_id:
                    return name
        except (OSError, ValueError):
            pass
        return None


# ------------------------------------------------------------------ the HTTP API


def certificate_user(escaped_pem, ca):
    """The common name in the client certificate nginx passed on, after checking it was issued
    by the CA for client use (nginx checked it too; this guards against anything else on the
    socket)."""
    import tempfile
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    pem = urllib.parse.unquote(escaped_pem).encode()
    with tempfile.NamedTemporaryFile(suffix='.pem') as f:
        f.write(pem)
        f.flush()
        r = subprocess.run(['openssl', 'verify', '-CAfile', ca, '-purpose', 'sslclient', f.name],
                           capture_output=True, text=True, timeout=10)
    if r.returncode != 0 or not r.stdout.strip().endswith(': OK'):
        return None
    cert = x509.load_pem_x509_certificate(pem)
    names = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    return names[0].value if names else None


def account_active(config_path, user):
    if not config_path:
        return True
    try:
        import psycopg
        import yaml
        with open(config_path) as f:
            uri = yaml.safe_load(f)['SQLALCHEMY_DATABASE_URI'].replace('postgresql+psycopg://', 'postgresql://')
        with psycopg.connect(uri, connect_timeout=5) as db:
            row = db.execute('SELECT active FROM "user" WHERE lower(username) = lower(%s)', (user,)).fetchone()
        return bool(row and row[0])
    except Exception as e:
        log.warning('account check for %r: %s', user, e)
        return False


MESH_GROUP = '239.255.64.73'          # TAK PTT mesh failover (UDP multicast on the local network)
MESH_PORT = 64739


WEEK = 7 * 86400


def mesh_epochs(data, channel_names, now=None):
    """This week's and next week's keys: [{'epoch', 'from', 'until', 'keys': {name: key}}]."""
    now = time.time() if now is None else now
    e = int(now // WEEK)
    return [{'epoch': k, 'from': k * WEEK, 'until': (k + 1) * WEEK, 'keys': mesh_keys(data, channel_names, k)}
            for k in (e, e + 1)]


def mesh_keys(data, channel_names, epoch):
    """{channel name: base64 AES-256 key} for one week, from the voice server's mesh secret."""
    import base64
    import hashlib
    import hmac
    path = os.path.join(data, 'mesh.secret')
    if not os.path.exists(path):
        tmp = path + '.tmp'
        with open(tmp, 'wb') as f:
            f.write(os.urandom(32))
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    with open(path, 'rb') as f:
        secret = f.read()
    return {name: base64.b64encode(hmac.new(secret, ('takptt-mesh:%d:%s' % (epoch, name)).encode(),
                                            hashlib.sha256).digest()).decode()
            for name in channel_names}


def make_handler(store, args, recorder=None):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *a):
            pass

        def deny(self, code, msg):
            body = json.dumps({'error': msg}).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def allowed(self):
            if self.headers.get('X-Ssl-Verify') != 'SUCCESS' or not self.headers.get('X-Ssl-Cert'):
                return None
            try:
                user = certificate_user(self.headers['X-Ssl-Cert'], args.ca)
            except Exception:
                return None
            return user if user and account_active(args.db_config, user) else None

        def do_GET(self):
            user = self.allowed()
            if not user:
                return self.deny(403, 'a PortableTAK certificate is needed')
            url = urllib.parse.urlparse(self.path)
            q = urllib.parse.parse_qs(url.query)
            path = url.path.rstrip('/')
            if path in ('/clips', '/api/clips'):
                since = float(q.get('since', ['0'])[0] or 0)
                body = json.dumps({'clips': store.list(since), 'now': time.time(),
                                   'days': args.days}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            if path in ('/keys', '/api/keys'):
                names = sorted({'LOBBY' if cid == 0 else n for cid, n in (recorder.channels.items() if recorder else [])})
                if not names:
                    return self.deny(503, 'not signed in to the voice server yet')
                body = json.dumps({'group': MESH_GROUP, 'port': MESH_PORT,
                                   'epochs': mesh_epochs(args.data, names)}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.send_header('Cache-Control', 'no-store')
                self.end_headers()
                self.wfile.write(body)
                return
            if path.startswith('/clips/') and path.endswith('.opus'):
                try:
                    f = store.file(int(path[len('/clips/'):-len('.opus')]))
                except ValueError:
                    f = None
                if not f or not os.path.exists(f):
                    return self.deny(404, 'no such clip')
                size = os.path.getsize(f)
                self.send_response(200)
                self.send_header('Content-Type', 'audio/ogg')
                self.send_header('Content-Length', str(size))
                self.send_header('Cache-Control', 'private, max-age=604800, immutable')
                self.end_headers()
                with open(f, 'rb') as fh:
                    self.wfile.write(fh.read())
                return
            self.deny(404, 'not found')

    return Handler


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', required=True)
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=64738)
    p.add_argument('--socket', help='Unix socket for the clip API (nginx proxies to it)')
    p.add_argument('--http', type=int, help='testing only: TCP port on 127.0.0.1 instead of --socket')
    p.add_argument('--ca', required=True, help="the TAK server's CA certificate (ca.pem)")
    p.add_argument('--ids')
    p.add_argument('--db-config')
    p.add_argument('--days', type=float, default=7)
    p.add_argument('--max-mb', type=float, default=2000)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    store = Store(args.data, args.days, args.max_mb)
    rec = Recorder(args, store)

    def housekeeping():
        while True:
            store.prune()
            time.sleep(3600)

    threading.Thread(target=rec.end_idle, daemon=True).start()
    threading.Thread(target=housekeeping, daemon=True).start()
    handler = make_handler(store, args, rec)
    if args.socket:
        import socketserver

        class UnixHTTPServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
            daemon_threads = True

            def get_request(self):
                conn, _ = super().get_request()
                return conn, ('local', 0)       # BaseHTTPRequestHandler wants a (host, port)

        try:
            os.unlink(args.socket)
        except OSError:
            pass
        old = os.umask(0o007)
        server = UnixHTTPServer(args.socket, handler)
        os.umask(old)
        try:
            import grp
            os.chown(args.socket, 0, grp.getgrnam('www-data').gr_gid)   # nginx's workers
        except (KeyError, PermissionError):
            pass
        where = args.socket
    else:
        server = ThreadingHTTPServer(('127.0.0.1', args.http or 18096), handler)
        where = '127.0.0.1:%d' % (args.http or 18096)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    log.info('clip API on %s', where)
    rec.run_forever()


if __name__ == '__main__':
    main()
