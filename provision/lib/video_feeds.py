"""Drone video feeds: let a drone push RTSP video to PortableTAK without a sign-in.

A drone controller such as the Skydio X10D ("Sharing > RTSP > External") pushes
its video to rtsp://<server>:8554/<name> and has no fields for a user name or
password. OpenTAKServer's video server (MediaMTX) asks OpenTAKServer to check a
sign-in for every publish and read, so such a push is refused. Each feed named
here may publish and be watched without a sign-in; every other stream still
needs one. The list lives on the drive (config/video-feeds.json), so it moves
with it, and is applied to MediaMTX at every start (its API changes the running
configuration; nothing is written into MediaMTX's own config file).

    python3 video_feeds.py apply /mnt/asgard
"""
import json
from pathlib import Path
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
    atomic_write(Path(root) / FEEDS, json.dumps({'format': 1, 'feeds': feeds}, indent=2) + '\n', 0o644)


def add(root, name, clock=time.time):
    name = str(name or '').strip()
    if not NAME.fullmatch(name):
        raise FeedError('invalid_feed_name')
    feeds = load(root)
    if any(f['name'] == name for f in feeds):
        return feeds
    if len(feeds) >= MAX_FEEDS:
        raise FeedError('too_many_feeds')
    feeds.append({'name': name, 'created': int(clock())})
    save(root, feeds)
    return feeds


def remove(root, name):
    feeds = [f for f in load(root) if f['name'] != name]
    save(root, feeds)
    return feeds


def exclusions(feeds):
    """MediaMTX authHTTPExclude: the defaults plus publish and read for each feed."""
    result = list(BASE_EXCLUDE)
    for feed in feeds:
        result.append({'action': 'publish', 'path': feed['name']})
        result.append({'action': 'read', 'path': feed['name']})
    return result


def _request(method, path, body=None, opener=urllib.request.urlopen):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(MEDIAMTX_API + path, data=data, method=method,
                                     headers={'Content-Type': 'application/json'})
    with opener(request, timeout=5) as response:
        text = response.read().decode() or '{}'
    return json.loads(text)


def apply(root='/mnt/asgard', opener=urllib.request.urlopen):
    """Push the current feed list into the running MediaMTX. True when applied."""
    _request('PATCH', '/v3/config/global/patch', {'authHTTPExclude': exclusions(load(root))}, opener)
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
    return [dict(feed, live=feed['name'] in live) for feed in load(root)]


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
