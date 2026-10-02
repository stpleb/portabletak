"""Idempotently prepare the disposable native test root inside its namespace."""

import ctypes
import os
from pathlib import Path
import sys

ROOT = Path(sys.argv[1]).resolve()
ROOTFS = ROOT / 'rootfs'
libc = ctypes.CDLL(None, use_errno=True)

def call(name, *args):
    if getattr(libc, name)(*args) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), name)

def mount(source, target, kind=None, flags=0, data=None):
    encode = lambda value: value.encode() if isinstance(value, str) else value
    call('mount', encode(source), encode(str(target)), encode(kind),
         ctypes.c_ulong(flags), encode(data))

def mounted(path):
    target = str(path)
    return any(line.split()[1].replace('\\040', ' ') == target
               for line in Path('/proc/mounts').read_text().splitlines())

for name in ('postgres', 'opentak', 'rabbitmq'):
    (ROOT / name).mkdir(mode=0o700, exist_ok=True)
for relative in ('dev', 'dev/pts', 'proc', 'sys', 'run', 'tmp',
                 'asgard/postgres', 'asgard/opentak', 'var/lib/rabbitmq'):
    (ROOTFS / relative).mkdir(parents=True, exist_ok=True)
for source, target, kind, flags, data in (
        ('/dev', ROOTFS/'dev', None, 4096, None),
        ('/dev/pts', ROOTFS/'dev/pts', None, 4096, None),
        ('proc', ROOTFS/'proc', 'proc', 2|4|8, None),
        ('/sys', ROOTFS/'sys', None, 4096, None),
        ('tmpfs', ROOTFS/'run', 'tmpfs', 2, 'mode=755'),
        ('tmpfs', ROOTFS/'tmp', 'tmpfs', 2, 'mode=1777'),
        (str(ROOT/'postgres'), ROOTFS/'asgard/postgres', None, 4096, None),
        (str(ROOT/'opentak'), ROOTFS/'asgard/opentak', None, 4096, None),
        (str(ROOT/'rabbitmq'), ROOTFS/'var/lib/rabbitmq', None, 4096, None)):
    if not mounted(target): mount(source, target, kind, flags, data)
