"""Read-only listing of cartridge backups for the management API."""
import json
import os
from pathlib import Path
import re
import time

NAME = re.compile(r'portabletak-(\d{8}_\d{6})\.tar\.gz')
NOTE = re.compile(r'[A-Za-z0-9 ._,:()-]{0,120}')


def valid_name(name):
    return isinstance(name, str) and NAME.fullmatch(name) is not None


def valid_note(note):
    return note is None or (isinstance(note, str) and NOTE.fullmatch(note) is not None)


def sidecar(path):
    try:
        if path.is_symlink() or path.stat().st_size > 4096:
            return {}
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) and data.get('format') == 1 else {}


def list_backups(root):
    root = Path(root)
    result = {'ok': True, 'backups': [], 'free_bytes': None}
    if not root.is_dir():
        return result
    usage = os.statvfs(root)
    result['free_bytes'] = usage.f_bavail * usage.f_frsize
    for entry in root.iterdir():
        match = NAME.fullmatch(entry.name)
        if not match or entry.is_symlink() or not entry.is_file():
            continue
        info = entry.stat()
        meta = sidecar(entry.with_name(entry.name + '.json'))
        created = time.strptime(match.group(1), '%Y%m%d_%H%M%S')
        note = meta.get('note') if valid_note(meta.get('note')) else None
        digest = meta.get('sha256')
        result['backups'].append({
            'name': entry.name,
            'size': info.st_size,
            'mtime': int(info.st_mtime),
            'created_local': meta.get('created_local') or time.strftime('%Y-%m-%dT%H:%M:%S', created),
            'label': time.strftime('%b %d, %Y %H:%M', created),
            'note': note or '',
            'sha256': digest if isinstance(digest, str) and re.fullmatch(r'[0-9a-f]{64}', digest) else None,
            'checksummed': bool(meta),
        })
    result['backups'].sort(key=lambda item: item['name'], reverse=True)
    return result
