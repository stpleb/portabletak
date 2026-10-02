#!/usr/bin/python3
"""Run host-side bootstrap tests with private PID, mount, IPC and network state."""

import argparse
import ctypes
import os
from pathlib import Path
import signal
import subprocess
import sys

libc = ctypes.CDLL(None, use_errno=True)

def call(name, *args):
    if getattr(libc, name)(*args) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), name)

def mount(source, target, kind=None, flags=0, data=None):
    encode = lambda value: value.encode() if isinstance(value, str) else value
    call('mount', encode(source), encode(str(target)), encode(kind),
         ctypes.c_ulong(flags), encode(data))

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('root')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    root = Path(args.root)
    rootfs = root / 'rootfs'
    if os.geteuid() != 0 or not args.command:
        parser.error('root privileges and an explicit command are required')
    if (root.resolve() != root.absolute() or not (root.parent / '.portabletak-development').is_file()
            or rootfs.is_symlink() or not rootfs.is_dir()):
        parser.error('a canonical marked development payload is required')
    flags = 0x00020000 | 0x20000000 | 0x04000000 | 0x08000000 | 0x40000000
    call('unshare', flags)
    mount(None, '/', flags=16384 | (1 << 18))
    child = os.fork()
    if child:
        def forward(signum, frame):
            try: os.kill(child, signum)
            except ProcessLookupError: pass
        signal.signal(signal.SIGTERM, forward)
        signal.signal(signal.SIGINT, forward)
        _, status = os.waitpid(child, 0)
        return os.waitstatus_to_exitcode(status)
    call('sethostname', b'portabletak-test', 16)
    subprocess.run(['/sbin/ip', 'link', 'set', 'lo', 'up'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    # The native test selects the OpenMANET adapter, so provide its service IP
    # only inside this private network namespace for nginx configuration checks.
    subprocess.run(['/sbin/ip', 'address', 'add', '10.41.250.10/32', 'dev', 'lo'], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    os.execvpe(args.command[0], args.command,
               {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8'})

if __name__ == '__main__':
    sys.exit(main())
