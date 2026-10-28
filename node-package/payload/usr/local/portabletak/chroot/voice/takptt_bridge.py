#!/usr/bin/env python3
"""OpenMANET talk group bridge for PortableTAK: joins voice server channels to OpenMANET talk groups.

For each link ("LOBBY=1": the server's LOBBY with talk group 1) it signs in to Murmur as
"OpenMANET CH 1" (its own certificate, which takptt_auth.py lets in by fingerprint), sits in
that channel and passes voice both ways: what is said in the channel goes out on the talk group,
and what is said on the talk group is said in the channel. So people on the server and people
only on the mesh (OpenMANET nodes, the ManetNode plugin, TAK PTT's OPENMANET groups) hear each
other. The Opus packets are passed on as they are (no re-encoding).

Talk groups use OpenMANET's own comms format: RTP, payload type 111 (Opus 48 kHz mono), sent to
the multicast address (239.41.0.1) on port 38799 + 2 x group (talk group 1 = 38801). One speaker
at a time each way, like a radio. Talk groups are not encrypted.

    takptt_bridge.py --data /asgard/opentak/voice --links LOBBY=1[,Ops=2] [--address 239.41.0.1]
                     [--interfaces br-ahwlan,wlan0] [--host 127.0.0.1] [--port 64738]

Only the Python standard library; the Mumble protocol is spoken directly (helpers shared with
takptt_recorder.py).
"""
import argparse
import hashlib
import logging
import os
import random
import re
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(1, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'recorder'))
from takptt_recorder import (VERSION, VERSION_MSG, AUTHENTICATE, PING, REJECT, SERVERSYNC,  # noqa: E402
                             CHANNELSTATE, CHANNELREMOVE, USERSTATE, UDPTUNNEL, pb, unpb,
                             mumble_varint, opus_samples)

log = logging.getLogger('takptt-bridge')

PT_OPUS = 111
QUIET = 0.6                                # seconds without packets that end a transmission
SKIP_INTERFACES = ('lo', 'tailscale', 'docker', 'veth', 'br-docker', 'virbr')


def talk_group_port(group):
    return 38799 + 2 * group


def name_for(group):
    return 'OpenMANET CH %d' % group


def mumble_varint_bytes(n):
    """Mumble's own varint (voice packets), for 0 <= n < 2**32."""
    if n < 0x80:
        return bytes([n])
    if n < 0x4000:
        return bytes([0x80 | (n >> 8), n & 0xFF])
    if n < 0x200000:
        return bytes([0xC0 | (n >> 16), (n >> 8) & 0xFF, n & 0xFF])
    if n < 0x10000000:
        return bytes([0xE0 | (n >> 24), (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])
    return bytes([0xF0]) + struct.pack('>I', n)


def voice_packet(seq, opus, last):
    """A client-to-server classic Mumble voice packet: Opus, normal talking (target 0)."""
    header = len(opus) | (0x2000 if last else 0)
    return bytes([4 << 5]) + mumble_varint_bytes(seq) + mumble_varint_bytes(header) + opus


def rtp_payload(b):
    """Offset of the Opus payload in an RTP packet, or -1 if it is not RTP payload type 111."""
    n = len(b)
    if n < 13 or (b[0] & 0xC0) != 0x80 or (b[1] & 0x7F) != PT_OPUS:
        return -1
    off = 12 + 4 * (b[0] & 0x0F)
    if b[0] & 0x10:
        if n < off + 4:
            return -1
        off += 4 + 4 * ((b[off + 2] << 8) | b[off + 3])
    pad = b[n - 1] if b[0] & 0x20 else 0
    return off if off < n - pad else -1


def interface_addresses(only=None):
    """IPv4 addresses of the interfaces to use for talk groups: the ones named, or every
    interface that has one except loopback, Tailscale, Docker and VM bridges."""
    out = []
    try:
        text = subprocess.run(['ip', '-4', '-o', 'addr', 'show'], capture_output=True, text=True).stdout
    except OSError:
        text = ''
    for line in text.splitlines():
        m = re.match(r'\d+:\s+(\S+)\s+inet\s+([\d.]+)/', line)
        if not m:
            continue
        name, addr = m.group(1).split('@')[0], m.group(2)
        if only:
            if name in only:
                out.append((name, addr))
        elif not name.startswith(SKIP_INTERFACES) and not addr.startswith('127.'):
            out.append((name, addr))
    return out


def parse_links(text):
    links = []
    for part in text.split(','):
        part = part.strip()
        if not part:
            continue
        channel, _, group = part.rpartition('=')
        group = int(group)
        if not channel or not 1 <= group <= 5:
            raise ValueError('a link is CHANNEL=GROUP with GROUP 1 to 5: %r' % part)
        links.append((channel, group))
    return links


class TalkGroup:
    """The multicast side of a link: receives the talk group, sends to it."""

    def __init__(self, address, group, interfaces):
        self.address, self.group, self.port = address, group, talk_group_port(group)
        self.interfaces = interfaces
        self.ssrc = random.getrandbits(32)
        self.seq = random.getrandbits(16)
        self.ts = random.getrandbits(32)
        self.first = True
        self.rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, 'SO_REUSEPORT'):
            self.rx.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
        self.rx.bind(('', self.port))
        for name, addr in interfaces:
            try:
                self.rx.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                                   socket.inet_aton(address) + socket.inet_aton(addr))
            except OSError as e:
                log.warning('talk group %d: cannot join on %s: %s', group, name, e)
        self.tx = []
        for name, addr in interfaces:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 32)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 0)
            s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(addr))
            self.tx.append(s)
        log.info('talk group %d on %s:%d via %s', group, address, self.port,
                 ', '.join(n for n, _ in interfaces) or 'no interface')

    def send(self, opus):
        """One Opus packet out on the talk group (as this bridge's own RTP stream)."""
        hdr = struct.pack('>BBHII', 0x80, (0x80 if self.first else 0) | PT_OPUS, self.seq, self.ts, self.ssrc)
        for s in self.tx:
            try:
                s.sendto(hdr + opus, (self.address, self.port))
            except OSError as e:
                log.debug('talk group %d send: %s', self.group, e)
        self.first = False
        self.seq = (self.seq + 1) & 0xFFFF
        self.ts = (self.ts + (opus_samples(opus) or 960)) & 0xFFFFFFFF

    def end(self):
        self.first = True                  # the next transmission starts with the marker bit


class Link:
    """One voice server channel joined to one talk group."""

    def __init__(self, args, channel, group):
        self.args, self.channel_name, self.group = args, channel, group
        self.name = name_for(group)
        self.tg = TalkGroup(args.address, group, args.ifaces)
        self.sock = None
        self.send_lock = threading.Lock()
        self.session = None
        self.channels = {}
        self.here = None
        # server -> talk group: one speaker at a time
        self.out_speaker, self.out_last = None, 0.0
        # talk group -> server
        self.in_ssrc, self.in_last, self.in_seq = None, 0.0, 0

    # -- identity and connection (as the recorder does)

    def identity(self):
        cert = os.path.join(self.args.data, 'bridge.pem')
        key = os.path.join(self.args.data, 'bridge.key')
        with self.args.identity_lock:
            if not (os.path.exists(cert) and os.path.exists(key)):
                subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes', '-days', '3650',
                                '-subj', '/CN=PortableTAK OpenMANET bridge', '-keyout', key, '-out', cert],
                               check=True, capture_output=True)
                os.chmod(key, 0o600)
            der = subprocess.run(['openssl', 'x509', '-in', cert, '-outform', 'DER'], check=True,
                                 capture_output=True).stdout
            with open(os.path.join(self.args.data, 'bridge.sha1'), 'w') as f:
                f.write(hashlib.sha1(der).hexdigest() + '\n')
        return cert, key

    def send(self, kind, payload):
        with self.send_lock:
            self.sock.sendall(struct.pack('>HI', kind, len(payload)) + payload)

    def run_forever(self):
        threading.Thread(target=self.talk_group_loop, daemon=True).start()
        threading.Thread(target=self.quiet_loop, daemon=True).start()
        while True:
            try:
                self.run_once()
            except Exception as e:
                log.warning('%s: voice server connection: %s', self.name, e)
            self.sock = None
            time.sleep(5)

    def run_once(self):
        cert, key = self.identity()
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE            # Murmur on this same machine
        ctx.load_cert_chain(cert, key)
        raw = socket.create_connection((self.args.host, self.args.port), timeout=20)
        sock = ctx.wrap_socket(raw)
        sock.settimeout(None)
        self.session, self.channels, self.here = None, {}, None
        self.sock = sock
        self.send(VERSION_MSG, pb([(1, VERSION), (2, '1.4.0'), (3, 'PortableTAK'), (4, 'bridge')]))
        self.send(AUTHENTICATE, pb([(1, self.name), (5, True)]))
        threading.Thread(target=self.pinger, args=(sock,), daemon=True).start()
        buf = b''
        while True:
            chunk = sock.recv(65536)
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

    def handle(self, kind, payload):
        if kind == UDPTUNNEL:
            self.from_server(payload)
            return
        if kind in (VERSION_MSG, PING):
            return
        m = unpb(payload)
        if kind == REJECT:
            raise ConnectionError('refused: %s' % (m.get(2, [b''])[0].decode(errors='replace')))
        if kind == SERVERSYNC:
            self.session = m[1][0]
            log.info('%s signed in to voice', self.name)
            self.join()
        elif kind == CHANNELSTATE:
            cid = m.get(1, [0])[0]
            if 3 in m:
                self.channels[cid] = m[3][0].decode(errors='replace')
            else:
                self.channels.setdefault(cid, str(cid))
            if self.session is not None:
                self.join()
        elif kind == CHANNELREMOVE:
            self.channels.pop(m.get(1, [0])[0], None)
        elif kind == USERSTATE and m.get(1, [None])[0] == self.session and 5 in m:
            self.here = m[5][0]

    def join(self):
        """Move into the linked channel (LOBBY is the server's root channel)."""
        want = 0 if self.channel_name.upper() == 'LOBBY' else None
        if want is None:
            for cid, name in self.channels.items():
                if cid != 0 and name == self.channel_name:
                    want = cid
                    break
        if want is None or want == self.here:
            return
        self.send(USERSTATE, pb([(1, self.session), (5, want)]))
        self.here = want
        log.info('%s joined %s, linked to talk group %d', self.name, self.channel_name, self.group)

    # -- server -> talk group

    def from_server(self, data):
        if not data or data[0] >> 5 != 4:          # Opus only
            return
        try:
            session, i = mumble_varint(data, 1)
            _seq, i = mumble_varint(data, i)
            header, i = mumble_varint(data, i)
            size, last = header & 0x1FFF, bool(header & 0x2000)
            opus = data[i:i + size]
        except (IndexError, struct.error):
            return
        if session == self.session:
            return
        now = time.time()
        if self.out_speaker not in (None, session) and now - self.out_last < QUIET:
            return                                 # someone else has the talk group
        if self.in_ssrc is not None and now - self.in_last < QUIET:
            return                                 # the talk group is talking into the channel
        self.out_speaker, self.out_last = session, now
        if opus:
            self.tg.send(opus)
        if last:
            self.out_speaker = None
            self.tg.end()

    # -- talk group -> server

    def talk_group_loop(self):
        self.tg.rx.settimeout(0.2)
        while True:
            try:
                data, _src = self.tg.rx.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError as e:
                log.warning('talk group %d receive: %s', self.group, e)
                time.sleep(1)
                continue
            off = rtp_payload(data)
            if off < 0:
                continue
            ssrc = struct.unpack('>I', data[8:12])[0]
            if ssrc == self.tg.ssrc:
                continue                           # our own (looped back by another interface)
            now = time.time()
            if self.in_ssrc not in (None, ssrc) and now - self.in_last < QUIET:
                continue                           # one speaker at a time
            if self.out_speaker is not None and now - self.out_last < QUIET:
                continue                           # the channel is talking out to the talk group
            if self.in_ssrc != ssrc:
                log.info('talk group %d -> %s', self.group, self.channel_name)
            self.in_ssrc, self.in_last = ssrc, now
            if self.sock is None or self.session is None:
                continue
            self.in_seq += 1
            try:
                self.send(UDPTUNNEL, voice_packet(self.in_seq, data[off:], False))
            except Exception as e:
                log.debug('%s: send voice: %s', self.name, e)

    def quiet_loop(self):
        """End a transmission that went quiet: the end flag into the channel, the marker bit next time out."""
        while True:
            time.sleep(0.2)
            now = time.time()
            if self.in_ssrc is not None and now - self.in_last > QUIET:
                self.in_ssrc = None
                if self.sock is not None and self.session is not None:
                    self.in_seq += 1
                    try:
                        self.send(UDPTUNNEL, voice_packet(self.in_seq, b'', True))
                    except Exception:
                        pass
            if self.out_speaker is not None and now - self.out_last > QUIET:
                self.out_speaker = None
                self.tg.end()


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--data', required=True)
    p.add_argument('--links', default='LOBBY=1', help='CHANNEL=GROUP[,CHANNEL=GROUP...]')
    p.add_argument('--address', default='239.41.0.1')
    p.add_argument('--interfaces', help='only these network interfaces (comma separated)')
    p.add_argument('--host', default='127.0.0.1')
    p.add_argument('--port', type=int, default=64738)
    args = p.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(name)s %(message)s')
    links = parse_links(args.links)
    if not links:
        log.info('no talk group links: bridge off')
        while True:
            time.sleep(3600)
    seen = set()
    for _, g in links:
        if g in seen:
            sys.exit('talk group %d is linked twice' % g)
        seen.add(g)
    args.ifaces = interface_addresses(set(args.interfaces.split(',')) if args.interfaces else None)
    args.identity_lock = threading.Lock()
    for channel, group in links:
        link = Link(args, channel, group)
        threading.Thread(target=link.run_forever, daemon=True).start()
    while True:
        time.sleep(3600)


if __name__ == '__main__':
    main()
