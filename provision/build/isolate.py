#!/usr/bin/env python3
"""Run a development rootfs with private mounts, PIDs, IPC and hostname.

Build mode shares outbound networking. Test mode creates a private network.
Never pass a live runtime rootfs: a new marker and canonical development path
are required. Mounts disappear when the namespace exits.
"""
import argparse
import ctypes
import os
from pathlib import Path
import signal
import sys

libc = ctypes.CDLL(None, use_errno=True)
def call(name, *args):
    if getattr(libc, name)(*args) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), name)

def mount(source, target, kind=None, flags=0, data=None):
    encode = lambda value: value.encode() if isinstance(value, str) else value
    call('mount', encode(source), encode(str(target)), encode(kind), ctypes.c_ulong(flags), encode(data))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--network', choices=('build', 'private'), default='private')
    parser.add_argument('root')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(args.root)
    if os.geteuid() != 0 or not args.command:
        parser.error('root privileges and an explicit command are required')
    if root.resolve() != root.absolute() or root.name != 'rootfs' or not (root.parent / '.portabletak-development').is_file():
        parser.error('a canonical marked development rootfs is required')
    for name in ('proc', 'dev', 'run', 'tmp'):
        path = root / name
        if path.is_symlink() or not path.is_dir():
            parser.error('mount destinations must be real directories')
    # NEWNS | NEWPID | NEWUTS | NEWIPC, optionally NEWNET.
    flags = 0x00020000 | 0x20000000 | 0x04000000 | 0x08000000
    if args.network == 'private':
        flags |= 0x40000000
    call('unshare', flags)
    mount(None, '/', flags=16384 | (1 << 18))  # recursive MS_PRIVATE
    child = os.fork()
    if child:
        def forward(signum, frame):
            try:
                os.kill(child, signum)
            except ProcessLookupError:
                pass
        signal.signal(signal.SIGTERM, forward)
        signal.signal(signal.SIGINT, forward)
        _, status = os.waitpid(child, 0)
        return os.waitstatus_to_exitcode(status)
    call('sethostname', b'portabletak-build', 17)
    mount('proc', root / 'proc', 'proc', 2 | 4 | 8)
    for name in ('dev', 'run', 'tmp'):
        mount('tmpfs', root / name, 'tmpfs', 2, 'mode=755' if name != 'tmp' else 'mode=1777')
    for name in ('null', 'zero', 'random', 'urandom'):
        target = root / 'dev' / name
        target.touch()
        mount('/dev/' + name, target, flags=4096)
    os.symlink('/proc/self/fd', root / 'dev/fd')
    for name, fd in (('stdin', 0), ('stdout', 1), ('stderr', 2)):
        os.symlink('/proc/self/fd/' + str(fd), root / 'dev' / name)
    # Optional clean cartridge layout. Bind only explicit directories from the
    # marked development tree, never anything from the active cartridge.
    for source_name, destination in (('opentak', 'asgard/opentak'), ('postgres', 'asgard/postgres'), ('rabbitmq', 'var/lib/rabbitmq')):
        source = root.parent / source_name
        target = root / destination
        if source.exists():
            if source.resolve() != source or not source.is_dir() or target.resolve() != target or not target.is_dir():
                raise ValueError('invalid_development_storage_bind')
            mount(str(source), target, flags=4096)
    os.chroot(root)
    os.chdir('/')
    os.environ.clear()
    os.environ.update(PATH='/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin', HOME='/root', LANG='C.UTF-8', DEBIAN_FRONTEND='noninteractive')
    os.execvpe(args.command[0], args.command, os.environ)

if __name__ == '__main__':
    sys.exit(main())
