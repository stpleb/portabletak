"""Offline map areas for the live map.

An area is a PMTiles file cut from the newest Protomaps world build (OpenStreetMap data)
for a bounding box and a maximum zoom, stored on the cartridge and served by nginx at
/maps/<file>. Downloading needs internet on the server; using the area does not.

    areas(root) / start(root, name, bbox, maxzoom) / delete(root, name)
"""
import json
import os
import platform
import re
import subprocess
import threading
import time
import urllib.request
from pathlib import Path

BUILDS = 'https://build-metadata.protomaps.dev/builds.json'
BUILD_URL = 'https://build.protomaps.com/{key}'
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9 ._-]{0,39}')
MAX_BYTES = 8 * 1024 ** 3          # one area: 8 GB at most
KEEP_FREE = 512 * 1024 ** 2        # leave room for the server itself
_lock = threading.Lock()
_jobs = {}                          # name -> {'progress', 'error', 'thread'}


def folder(root):
    return Path(root) / 'opentak/maps'


def _index_path(root):
    return folder(root) / 'areas.json'


def _load(root):
    try:
        return json.loads(_index_path(root).read_text())
    except (OSError, ValueError):
        return {}


def _save(root, index):
    path = _index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(index, indent=1, sort_keys=True))
    os.replace(tmp, path)


def file_for(name):
    return re.sub(r'[^A-Za-z0-9._-]', '_', name) + '.pmtiles'


def tool():
    arch = {'aarch64': 'aarch64', 'arm64': 'aarch64', 'x86_64': 'x86_64', 'amd64': 'x86_64'}.get(platform.machine())
    path = Path('/usr/local/portabletak/share/pmtiles') / (arch or 'none') / 'pmtiles'
    if not path.is_file():
        raise RuntimeError('map_tool_missing')
    return str(path)


def areas(root):
    index = _load(root)
    out = []
    for name, meta in sorted(index.items()):
        entry = dict(meta, name=name)
        job = _jobs.get(name)
        if job and not entry.get('ready'):
            entry['progress'], entry['error'] = job.get('progress', 0), job.get('error')
        elif not entry.get('ready') and not job:
            entry['error'] = entry.get('error') or 'interrupted'
        out.append(entry)
    return out


def latest_build():
    request = urllib.request.Request(BUILDS, headers={'User-Agent': 'PortableTAK (offline map areas)'})
    with urllib.request.urlopen(request, timeout=20) as r:
        builds = json.load(r)
    key = sorted(b['key'] for b in builds if str(b.get('key', '')).endswith('.pmtiles'))[-1]
    return BUILD_URL.format(key=key)


def _check(name, bbox, maxzoom):
    if not NAME.fullmatch(name or ''):
        raise ValueError('invalid_area_name')
    try:
        west, south, east, north = (float(v) for v in bbox)
    except (TypeError, ValueError):
        raise ValueError('invalid_area_bbox')
    if not (-180 <= west < east <= 180 and -85 <= south < north <= 85):
        raise ValueError('invalid_area_bbox')
    maxzoom = int(maxzoom)
    if not 6 <= maxzoom <= 16:
        raise ValueError('invalid_area_zoom')
    return [round(west, 5), round(south, 5), round(east, 5), round(north, 5)], maxzoom


def _estimate(binary, source, bbox, maxzoom):
    """Bytes the extract would download (pmtiles --dry-run)."""
    out = subprocess.run([binary, 'extract', source, '/dev/null', '--bbox=' + ','.join(map(str, bbox)),
                          '--maxzoom=%d' % maxzoom, '--dry-run'], capture_output=True, text=True, timeout=300)
    found = re.findall(r'archive size of ([0-9.]+) (B|kB|KB|MB|GB|TB)', out.stdout + out.stderr)
    if not found:
        raise RuntimeError('map_size_unknown')
    value, unit = found[-1]
    return int(float(value) * {'B': 1, 'kB': 1e3, 'KB': 1e3, 'MB': 1e6, 'GB': 1e9, 'TB': 1e12}[unit])


def start(root, name, bbox, maxzoom):
    """Check the area and start downloading it in the background. Returns its estimate."""
    name = (name or '').strip()
    bbox, maxzoom = _check(name, bbox, maxzoom)
    with _lock:
        if name in _jobs and _jobs[name].get('thread') and _jobs[name]['thread'].is_alive():
            raise ValueError('area_download_running')
        index = _load(root)
        if name in index and index[name].get('ready'):
            raise ValueError('area_exists')
    binary = tool()
    try:
        source = latest_build()
    except OSError:
        raise RuntimeError('no_internet_on_server')
    size = _estimate(binary, source, bbox, maxzoom)
    target = folder(root)
    target.mkdir(parents=True, exist_ok=True)
    stat = os.statvfs(target)
    free = stat.f_bavail * stat.f_frsize
    if size > MAX_BYTES:
        raise ValueError('area_too_large:%d' % size)
    if size + KEEP_FREE > free:
        raise ValueError('not_enough_space:%d' % size)
    with _lock:
        index = _load(root)
        index[name] = {'file': file_for(name), 'bbox': bbox, 'maxzoom': maxzoom, 'size': size,
                       'ready': False, 'source': source.rsplit('/', 1)[-1], 'started': int(time.time())}
        _save(root, index)
        job = {'progress': 0, 'error': None}
        job['thread'] = threading.Thread(target=_download, args=(root, name, binary, source, bbox, maxzoom, job),
                                         name='map-area-' + name, daemon=True)
        _jobs[name] = job
        job['thread'].start()
    return {'name': name, 'size': size}


def _download(root, name, binary, source, bbox, maxzoom, job):
    final = folder(root) / file_for(name)
    part = final.with_suffix('.part')
    try:
        proc = subprocess.Popen([binary, 'extract', source, str(part), '--bbox=' + ','.join(map(str, bbox)),
                                 '--maxzoom=%d' % maxzoom], stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        buffer = b''
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            buffer = (buffer + chunk)[-512:]
            found = re.findall(rb'(\d+)%', buffer)
            if found:
                job['progress'] = min(99, int(found[-1]))
        if proc.wait() != 0 or not part.is_file():
            raise RuntimeError('download_failed')
        os.replace(part, final)
        with _lock:
            index = _load(root)
            if name in index:
                index[name].update(ready=True, size=final.stat().st_size, finished=int(time.time()))
                index[name].pop('error', None)
                _save(root, index)
        job['progress'] = 100
    except Exception as error:   # report it in the list; never crash the API
        job['error'] = str(error)[:200]
        part.unlink(missing_ok=True)
        with _lock:
            index = _load(root)
            if name in index:
                index[name]['error'] = job['error']
                _save(root, index)


def delete(root, name):
    with _lock:
        job = _jobs.get(name)
        if job and job.get('thread') and job['thread'].is_alive():
            raise ValueError('area_download_running')
        index = _load(root)
        meta = index.pop(name, None)
        if meta is None:
            raise ValueError('area_not_found')
        for suffix in ('.pmtiles', '.part'):
            (folder(root) / (Path(meta['file']).stem + suffix)).unlink(missing_ok=True)
        _save(root, index)
        _jobs.pop(name, None)
