"""Stop a missing cartridge's processes using host /proc only.

Never opens cartridge files or executes code from a damaged rootfs. pidfds keep
signals tied to the inspected process even if a numeric PID is reused.
"""
import os
from pathlib import Path
import select
import signal
import time

from host_profile import rootfs as _rootfs

# Both trees of a dual-chip cartridge; only one runs on a given host.
ROOTFS_ALL = ('/mnt/asgard/rootfs', '/mnt/asgard/arch/amd64/rootfs')
ROOTFS = str(_rootfs('/mnt/asgard'))
EXECUTABLE_PREFIX = '/mnt/asgard/'

def belongs_to_cartridge(root, executable):
    roots = {r for base in ROOTFS_ALL for r in (base, base + ' (deleted)')}
    return root in roots or executable.startswith(EXECUTABLE_PREFIX)

def collect(proc=Path('/proc')):
    handles = []
    for entry in proc.iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        descriptor = None
        try:
            descriptor = os.pidfd_open(int(entry.name))
            root = os.readlink(entry / 'root')
            executable = os.readlink(entry / 'exe')
            if belongs_to_cartridge(root, executable):
                handles.append(descriptor)
                descriptor = None
        except (ProcessLookupError, FileNotFoundError, PermissionError):
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return handles

def stop(grace=20):
    if os.geteuid() != 0:
        raise PermissionError('root_required')
    handles = collect()
    try:
        for descriptor in handles:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGTERM)
            except ProcessLookupError:
                pass
        deadline = time.monotonic() + grace
        remaining = set(handles)
        while remaining and time.monotonic() < deadline:
            ready, _, _ = select.select(list(remaining), [], [], min(1, max(0, deadline - time.monotonic())))
            remaining.difference_update(ready)
        for descriptor in remaining:
            try:
                signal.pidfd_send_signal(descriptor, signal.SIGKILL)
            except ProcessLookupError:
                pass
        # Rescan to catch children spawned during graceful shutdown.
        second = collect()
        try:
            for descriptor in second:
                try:
                    signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            deadline = time.monotonic() + 5
            pending = set(second)
            while pending and time.monotonic() < deadline:
                ready, _, _ = select.select(list(pending), [], [], 0.2)
                pending.difference_update(ready)
            return {'targeted': len(handles), 'remaining': len(pending)}
        finally:
            for descriptor in second:
                os.close(descriptor)
    finally:
        for descriptor in handles:
            os.close(descriptor)
