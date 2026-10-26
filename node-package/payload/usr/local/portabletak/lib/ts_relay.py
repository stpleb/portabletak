"""Unwrap MPEG-TS carried in RTSP and hand it to a local UDP port.

TAK ICU ("Local Area Network" with delivery "RTSP") pushes RTSP reliably, but as one
MPEG-TS track that MediaMTX passes on without unpacking, so browsers cannot play it.
Its plain UDP delivery is unpacked, but loses packets over Wi-Fi. MediaMTX runs this
when TAK ICU starts pushing to _in/<feed>: it reads that stream over RTSP (TCP, on
this machine) and sends the MPEG-TS to the feed's UDP port on 127.0.0.1, where
MediaMTX unpacks it as the feed itself.

    python3 ts_relay.py PATH UDP_PORT   (RTSP_PORT from MediaMTX's environment, else 8554)
"""
import json
import os
import re
import socket
import sys
import urllib.request

API = 'http://127.0.0.1:9997'


def request(sock, method, url, cseq, headers=()):
    lines = ['%s %s RTSP/1.0' % (method, url), 'CSeq: %d' % cseq, 'User-Agent: portabletak-ts-relay']
    sock.sendall(('\r\n'.join(lines + list(headers)) + '\r\n\r\n').encode())


def read_response(stream):
    """(status, headers, body) of the next RTSP response."""
    status = stream.readline().decode('latin-1').strip()
    if not status.startswith('RTSP/'):
        raise ConnectionError('unexpected reply: %r' % status[:60])
    headers = {}
    while True:
        line = stream.readline().decode('latin-1').strip()
        if not line:
            break
        key, _, value = line.partition(':')
        headers[key.strip().lower()] = value.strip()
    body = stream.read(int(headers.get('content-length', 0) or 0))
    code = int(status.split()[1])
    if code != 200:
        raise ConnectionError('RTSP %d' % code)
    return headers, body.decode('latin-1')


def control_url(base, sdp):
    """The track URL from the SDP (absolute, or relative to the stream URL)."""
    in_media = False
    for line in sdp.splitlines():
        if line.startswith('m='):
            in_media = True
        if line.startswith('a=control:') and in_media:
            control = line[len('a=control:'):].strip()
            if control.startswith('rtsp://'):
                return control
            return base.rstrip('/') + '/' + control
    return base


def rtp_payload(packet):
    if len(packet) < 12 or packet[0] >> 6 != 2:
        return b''
    offset = 12 + 4 * (packet[0] & 0x0F)
    if packet[0] & 0x10 and len(packet) >= offset + 4:   # header extension
        offset += 4 + 4 * int.from_bytes(packet[offset + 2:offset + 4], 'big')
    end = len(packet)
    if packet[0] & 0x20 and end > offset:                # padding
        end -= packet[-1]
    return packet[offset:end]


def restart_feed(name, opener=urllib.request.urlopen):
    """Start the feed afresh: TAK ICU's timestamps begin again at each broadcast, which
    players of a feed that just carries on cannot follow."""
    def call(method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        with opener(urllib.request.Request(API + path, data=data, method=method,
                                           headers={'Content-Type': 'application/json'}), timeout=5) as r:
            return json.loads(r.read().decode() or '{}')
    conf = call('GET', '/v3/config/paths/get/' + name)
    keep = {k: conf[k] for k in ('source',) if k in conf}
    call('DELETE', '/v3/config/paths/delete/' + name)
    call('POST', '/v3/config/paths/add/' + name, keep)


def relay(path, udp_port, rtsp_port=8554):
    try:
        restart_feed(path.split('/', 1)[1])
    except (OSError, ValueError):
        pass                                             # still relay; players may stall
    url = 'rtsp://127.0.0.1:%d/%s' % (rtsp_port, path)
    sock = socket.create_connection(('127.0.0.1', rtsp_port), timeout=10)
    stream = sock.makefile('rb')
    request(sock, 'DESCRIBE', url, 1, ['Accept: application/sdp'])
    _, sdp = read_response(stream)
    request(sock, 'SETUP', control_url(url, sdp), 2, ['Transport: RTP/AVP/TCP;unicast;interleaved=0-1'])
    headers, _ = read_response(stream)
    session = headers.get('session', '').split(';')[0]
    request(sock, 'PLAY', url, 3, ['Session: ' + session])
    read_response(stream)
    sock.settimeout(30)
    out = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    target = ('127.0.0.1', udp_port)
    while True:
        first = stream.read(1)
        if not first:
            return
        if first != b'$':                                # an RTSP message: skip it
            stream.readline()
            while stream.readline().strip():
                pass
            continue
        head = stream.read(3)
        if len(head) < 3:
            return
        packet = stream.read(int.from_bytes(head[1:3], 'big'))
        if head[0] == 0:
            data = rtp_payload(packet)
            for i in range(0, len(data) - len(data) % 188, 1316):   # 7 TS packets per datagram
                out.sendto(data[i:i + 1316], target)


if __name__ == '__main__':
    if len(sys.argv) != 3 or not re.fullmatch(r'_in/[a-z0-9][a-z0-9-]{0,31}', sys.argv[1]) \
            or not sys.argv[2].isdigit():
        print('usage: ts_relay.py _in/NAME UDP_PORT', file=sys.stderr)
        sys.exit(2)
    try:
        relay(sys.argv[1], int(sys.argv[2]), int(os.environ.get('RTSP_PORT') or 8554))
    except (OSError, ConnectionError, ValueError) as error:
        print('ts_relay: %s' % error, file=sys.stderr)
        sys.exit(1)
