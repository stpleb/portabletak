"""Drone video feeds: let a drone push RTSP video to PortableTAK without a sign-in.

A drone controller such as the Skydio X10D ("Sharing > RTSP > External") pushes
its video to rtsp://<server>:8554/<name> and has no fields for a user name or
password. OpenTAKServer's video server (MediaMTX) asks OpenTAKServer to check a
sign-in for every publish and read, so such a push is refused. Each feed named
here may publish and be watched without a sign-in; every other stream still
needs one. The list lives on the drive (config/video-feeds.json), so it moves
with it, and is applied to MediaMTX at every start (its API changes the running
configuration; nothing is written into MediaMTX's own config file).

TAK ICU wraps its video in MPEG-TS, which MediaMTX passes on from an RTSP push but
cannot turn into HLS or WebRTC, and its plain UDP loses packets over Wi-Fi. A TAK
ICU feed ("udp_port") therefore takes TAK ICU's RTSP push (over TCP) at the hidden
path _in/<feed>; ts_relay.py unwraps it into the feed's internal UDP port
(5600-5615, loopback only), where MediaMTX unpacks it into the feed itself.

A feed can also pull from a camera or another server (an RTSP Wi-Fi/IP camera,
RTMP, SRT or an HLS link): MediaMTX connects to the URL itself and passes the
video on under the feed's name. Such URLs often hold a camera password, so the
list is kept readable by root only and the dashboard shows URLs without it.

    python3 video_feeds.py apply /mnt/asgard
"""
import json
from pathlib import Path
from urllib.parse import urlsplit
import re
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parent))
from portabletak_provision import atomic_write

FEEDS = 'config/video-feeds.json'
NAME = re.compile(r'[a-z0-9][a-z0-9-]{0,31}')   # Skydio stream names: letters, digits, dashes
MAX_FEEDS = 16
MEDIAMTX_API = 'http://127.0.0.1:9997'
UDP_PORTS = range(5600, 5600 + MAX_FEEDS)
# TAK ICU's RTSP for a UDP feed arrives here; ts_relay.py unwraps it into the feed's UDP port.
INPUT = '_in/'
RELAY = str(Path(__file__).resolve().parent / 'ts_relay.py')
# Where MediaMTX can pull from (no spaces or quotes; it gets the URL as it is).
SOURCE = re.compile(r'(rtsps?|rtmps?|srt|https?)://[^\s"\'<>\\]{1,500}')
# What mediamtx.yml already excludes from sign-in; kept when feeds are added.
BASE_EXCLUDE = [{'action': 'api'}, {'action': 'metrics'}, {'action': 'pprof'}]


class FeedError(ValueError):
    pass


def load(root='/mnt/asgard'):
    try:
        data = json.loads((Path(root) / FEEDS).read_text())
    except (OSError, ValueError):
        return []
    feeds = data.get('feeds') if isinstance(data, dict) else None
    return [f for f in feeds or [] if isinstance(f, dict) and NAME.fullmatch(str(f.get('name', '')))]


def save(root, feeds):
    atomic_write(Path(root) / FEEDS, json.dumps({'format': 1, 'feeds': feeds}, indent=2) + '\n', 0o600)


def add(root, name, clock=time.time, udp=False, source=None):
    """Add a feed: pushed to over RTSP (the default), sent to over UDP (udp=True, gets
    a UDP port) or pulled from a URL (source). An existing feed keeps its name and
    switches type."""
    name = str(name or '').strip()
    if not NAME.fullmatch(name):
        raise FeedError('invalid_feed_name')
    source = str(source or '').strip() or None
    if source and not SOURCE.fullmatch(source):
        raise FeedError('invalid_source_url')
    if source and udp:
        raise FeedError('invalid_feed_type')
    feeds = load(root)
    feed = next((f for f in feeds if f['name'] == name), None)
    if feed is None:
        if len(feeds) >= MAX_FEEDS:
            raise FeedError('too_many_feeds')
        feed = {'name': name, 'created': int(clock())}
        feeds.append(feed)
    elif bool(feed.get('udp_port')) == bool(udp) and feed.get('source') == source:
        return feeds
    if source:
        feed['source'] = source
    else:
        feed.pop('source', None)
    if udp:
        used = {f.get('udp_port') for f in feeds}
        feed['udp_port'] = next(p for p in UDP_PORTS if p not in used)
    else:
        feed.pop('udp_port', None)
    save(root, feeds)
    return feeds


def public(feed):
    """A feed as the dashboard shows it: a pulled URL without its password."""
    feed = dict(feed)
    if feed.get('source'):
        parts = urlsplit(feed['source'])
        if parts.password is not None:
            host = parts.netloc.rsplit('@', 1)[1]
            feed['source'] = parts._replace(netloc='%s:***@%s' % (parts.username, host)).geturl()
    return feed


def remove(root, name):
    feeds = [f for f in load(root) if f['name'] != name]
    save(root, feeds)
    return feeds


def exclusions(feeds):
    """MediaMTX authHTTPExclude: the defaults plus publish and read for each feed."""
    result = list(BASE_EXCLUDE)
    for feed in feeds:
        for path in [feed['name']] + ([INPUT + feed['name']] if feed.get('udp_port') else []):
            result.append({'action': 'publish', 'path': path})
            result.append({'action': 'read', 'path': path})
    return result


def webrtc_hosts(root):
    """Addresses MediaMTX offers browsers for WebRTC (Watch) besides its own interfaces:
    the address phones use and the Tailscale one. On the Mac and Windows hosts MediaMTX
    runs inside a VM whose own address a browser cannot reach."""
    try:
        import client_packages
        import server_addresses
        record = server_addresses.current(root)
        hosts = [record.get('advertise'), client_packages.remote_address(record)]
    except Exception:
        return []
    return [h for h in dict.fromkeys(hosts) if isinstance(h, str) and re.fullmatch(r'[A-Za-z0-9.:-]{1,253}', h)]


def udp_source(port):
    # Loopback only: just ts_relay.py feeds it. TAK ICU also keeps sending plain UDP
    # to the server in RTSP mode, which would otherwise mix into the feed.
    return 'udp://127.0.0.1:%d' % port


def _ours(source):
    """A path configuration this module made for a UDP feed (also the older form that
    listened on every address)."""
    return any(source in (udp_source(p), 'udp://0.0.0.0:%d' % p) for p in UDP_PORTS)


def _request(method, path, body=None, opener=urllib.request.urlopen):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(MEDIAMTX_API + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
    with opener(request, timeout=5) as response:
        text = response.read().decode() or '{}'
    return json.loads(text)


def apply(root='/mnt/asgard', opener=urllib.request.urlopen, sleep=time.sleep, forget=()):
    """Push the current feed list into the running MediaMTX. True when applied.
    forget: names of feeds just removed, whose pull configuration goes too."""
    feeds = load(root)
    settings = {'authHTTPExclude': exclusions(feeds), 'webrtcAdditionalHosts': webrtc_hosts(root)}
    if any(f.get('udp_port') for f in feeds):
        # TAK ICU pushes RTP over UDP when offered and loses packets over Wi-Fi (smeared
        # video); offered only TCP, it uses TCP. Senders without TCP cannot push then.
        settings['rtspTransports'] = ['tcp']
    else:
        settings['rtspTransports'] = ['udp', 'multicast', 'tcp']   # MediaMTX's default
    _request('PATCH', '/v3/config/global/patch', settings, opener)
    want = {}
    for f in feeds:
        if f.get('udp_port') in UDP_PORTS:
            want[f['name']] = {'source': udp_source(f['udp_port'])}
            # Hidden input for TAK ICU's RTSP (no OpenTAKServer hooks: it is not a feed itself).
            want[INPUT + f['name']] = {'source': 'publisher', 'runOnReady': '/usr/bin/python3 %s %s%s %d' % (
                RELAY, INPUT, f['name'], f['udp_port']), 'runOnReadyRestart': True, 'runOnNotReady': '',
                'runOnRead': '', 'runOnUnread': ''}
        elif f.get('source'):
            want[f['name']] = {'source': f['source']}
    mine = {f['name'] for f in feeds} | set(forget)
    # A global change restarts MediaMTX's API listener: wait for it to answer again.
    for attempt in range(20):
        try:
            listed = _request('GET', '/v3/config/paths/list?itemsPerPage=1000', opener=opener)
            break
        except (OSError, urllib.error.URLError):
            if attempt == 19:
                raise
            sleep(0.25)
    have = {item.get('name'): item for item in listed.get('items', [])}
    for name, item in have.items():
        source = item.get('source')
        if name not in want and (_ours(source) or str(name).startswith(INPUT) or
                                 (name in mine and source not in (None, '', 'publisher'))):
            _request('DELETE', '/v3/config/paths/delete/' + name, opener=opener)
    for name, conf in want.items():
        if name not in have:
            _request('POST', '/v3/config/paths/add/' + name, conf, opener)
        elif any(have[name].get(k) != v for k, v in conf.items()):
            _request('PATCH', '/v3/config/paths/patch/' + name, conf, opener)
    return True


def status(root='/mnt/asgard', opener=urllib.request.urlopen):
    """Each feed with live = True while a drone is publishing to it."""
    live = set()
    try:
        for item in _request('GET', '/v3/paths/list', opener=opener).get('items', []):
            if item.get('ready'):
                live.add(item.get('name'))
    except (OSError, ValueError, urllib.error.URLError):
        pass
    return [dict(public(feed), live=feed['name'] in live) for feed in load(root)]


if __name__ == '__main__':
    root = sys.argv[2] if len(sys.argv) > 2 else '/mnt/asgard'
    if len(sys.argv) > 1 and sys.argv[1] == 'apply':
        try:
            apply(root)
            print('PORTABLETAK_VIDEO_FEEDS=APPLIED')
            print('PORTABLETAK_VIDEO_FEED_COUNT=%d' % len(load(root)))
        except (OSError, ValueError, urllib.error.URLError) as error:
            print('PORTABLETAK_VIDEO_FEEDS=FAILED')
            print('PORTABLETAK_VIDEO_FEEDS_ERROR=' + type(error).__name__)
            sys.exit(1)
    else:
        print('usage: video_feeds.py apply [ROOT]', file=sys.stderr)
        sys.exit(2)
