"""The logo at the top of the dashboard. A custom one lives on the drive (so updates keep it
and backups carry it); without one the dashboard shows the logo built into the package."""
import os
from pathlib import Path
import secrets

DATA = Path('/mnt/asgard/opentak/data')
BUILT_IN = Path('/usr/local/portabletak/web/logo.png')
MAX_BYTES = 2 * 1024 * 1024


def media_type(head):
    """By the file's first bytes, never its name. SVG is left out on purpose: it can carry scripts."""
    if head.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if head.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if head[:4] == b'RIFF' and head[8:12] == b'WEBP':
        return 'image/webp'
    return None


def custom_path(data=None):
    return Path(data or DATA) / 'portabletak-logo/logo'


def current(data=None):
    """(path, media type) of what to show. A damaged custom file falls back to the built-in logo."""
    path = custom_path(data)
    try:
        with path.open('rb') as image:
            kind = media_type(image.read(16))
    except OSError:
        kind = None
    return (path, kind) if kind else (BUILT_IN, 'image/png')


def is_custom(data=None):
    return current(data)[0] != BUILT_IN


def save(content, data=None):
    """Returns None when saved, or the error name. Replaces the old logo in one step."""
    if not content:
        return 'empty_file'
    if len(content) > MAX_BYTES:
        return 'logo_too_large'
    if not media_type(content[:16]):
        return 'not_an_image'
    root = Path(data or DATA)
    if not root.is_dir():
        return 'no_drive'   # /mnt/asgard with no drive mounted is the node's own small disk
    path = custom_path(root)
    path.parent.mkdir(exist_ok=True)
    staged = path.with_name('.' + secrets.token_hex(8))
    try:
        staged.write_bytes(content)
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)
    return None


def clear(data=None):
    custom_path(data).unlink(missing_ok=True)
