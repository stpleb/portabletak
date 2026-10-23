"""Start the upstream OTS EUD handler with its device-registration race fixed.

OpenTAKServer 1.7.13 registers a connected device (euds row, per-device
RabbitMQ queue and bindings) from the first message only, and it records the
device uid before checking that the connection's RabbitMQ channel is open. On a
Raspberry Pi each connection builds a Flask app first, so the channel is often
not open yet: the uid is set, registration is skipped, and it is never retried.
That device then receives nothing and its CoT is dropped by the parser.

This launcher defers registration until the channel is open, leaving the uid
unset so the next message retries. The sealed package is not modified.

It also makes a device with more than one live connection (for example a
client package with both a Wi-Fi and a Tailscale connection enabled, or a new
connection opened before the old one timed out) safe. Upstream names the
device's RabbitMQ queue after its uid, so every connection of that device shares
it; when any one connection closes, upstream unbinds that shared queue and
broadcasts "disconnected", so the device's remaining connection stops receiving
and other clients grey it out. Each forked connection now records itself in
REGISTRY; a closing connection that still has a live sibling only closes its own
socket and channel.

Connections silent for IDLE_SECONDS are closed (dead phones no longer linger).

Usage (inside the cartridge chroot):
    python eud_handler_launch.py /asgard/opentak/venv/bin/eud_handler [args...]
"""
import hashlib
import os
import re
import sys

REGISTRY = '/tmp/portabletak-eud-connections'


def _key(uid):
    if re.fullmatch(r'[A-Za-z0-9._-]{1,100}', uid):
        return uid
    return 'h-' + hashlib.sha256(uid.encode()).hexdigest()[:32]


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def register(uid, registry=REGISTRY, pid=None):
    pid = pid or os.getpid()
    os.makedirs(registry, mode=0o700, exist_ok=True)
    path = os.path.join(registry, f'{_key(uid)}.{pid}')
    with open(path, 'w'):
        pass
    return path


def unregister(path):
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


def live_siblings(uid, own_path, registry=REGISTRY, alive=_alive):
    prefix = _key(uid) + '.'
    count = 0
    try:
        names = os.listdir(registry)
    except FileNotFoundError:
        return 0
    for name in names:
        path = os.path.join(registry, name)
        if not name.startswith(prefix) or path == own_path:
            continue
        pid = name[len(prefix):]
        if pid.isdigit() and alive(int(pid)):
            count += 1
        else:
            unregister(path)
    return count


def install_duplicate_guard(handler_class, registry=REGISTRY, alive=_alive):
    original_parse = handler_class.parse_device_info
    original_close = handler_class.close_connection
    if getattr(original_close, '_portabletak_duplicate_guard', False):
        return False

    def parse_device_info(self, event):
        result = original_parse(self, event)
        uid = getattr(self, 'uid', None)
        if uid and getattr(self, '_portabletak_registered', None) is None:
            try:
                self._portabletak_registered = register(uid, registry)
            except OSError:
                self._portabletak_registered = False
        return result

    def close_connection(self):
        # Upstream closes twice when it rejects a login (once in handle_auth, again when
        # its loop ends); the second time the RabbitMQ channel is already closed.
        if getattr(self, '_portabletak_closed', False):
            return None
        self._portabletak_closed = True
        path = getattr(self, '_portabletak_registered', None)
        uid = getattr(self, 'uid', None)
        if path:
            unregister(path)
            self._portabletak_registered = False
        if not (path and uid and live_siblings(uid, path, registry, alive)):
            try:
                return original_close(self)
            except Exception:
                # Channel already gone: just close the socket.
                self.shutdown = True
                try:
                    self.request.shutdown(2)
                except OSError:
                    pass
                self.request.close()
                return None
        # Another connection of this device is still live: keep its queue
        # bindings and do not announce a disconnect.
        self.logger.info('{} disconnected (device {} still connected on another connection)'.format(
            self.client_address[0], uid))
        channel = getattr(self, 'rabbit_channel', None)
        if channel and not channel.is_closing and not channel.is_closed:
            channel.close()
        if not self.shutdown:
            self.shutdown = True
            try:
                self.request.shutdown(2)
            except OSError:
                pass
            self.request.close()
        return None

    parse_device_info._portabletak_duplicate_guard = True
    close_connection._portabletak_duplicate_guard = True
    handler_class.parse_device_info = parse_device_info
    handler_class.close_connection = close_connection
    return True


# A TAK app sends something at least every few seconds (its position, or a
# "t-x-c-t" ping the server answers). Upstream waits forever, so a phone that
# vanishes without closing its connection (Wi-Fi off, out of range, asleep)
# stays "connected" for hours and piles up on every reconnect. A connection that
# is silent this long is dead: close it (a live app simply reconnects).
IDLE_SECONDS = 120


def install_idle_timeout(handler_class, idle=IDLE_SECONDS):
    original = handler_class.handle
    if getattr(original, '_portabletak_idle', False):
        return False

    def handle(self):
        import socket
        try:
            self.request.settimeout(idle)
            self.request.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for name, value in (('TCP_KEEPIDLE', 60), ('TCP_KEEPINTVL', 15), ('TCP_KEEPCNT', 4)):
                if hasattr(socket, name):
                    self.request.setsockopt(socket.IPPROTO_TCP, getattr(socket, name), value)
        except OSError:
            pass
        return original(self)

    handle._portabletak_idle = True
    handler_class.handle = handle
    return True


def install(handler_class):
    original = handler_class.parse_device_info
    if getattr(original, '_portabletak_guarded', False):
        return False

    def parse_device_info(self, event):
        channel = getattr(self, 'rabbit_channel', None)
        if not (channel and channel.is_open):
            return None
        return original(self, event)

    parse_device_info._portabletak_guarded = True
    handler_class.parse_device_info = parse_device_info
    return True


def latest_positions(app, own_uid, now=None):
    """The newest unexpired position report (CoT type a-*) of every other device."""
    import datetime
    from opentakserver.extensions import db
    from opentakserver.models.CoT import CoT
    from opentakserver.models.EUD import EUD
    now = now or datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    result = []
    with app.app_context():
        for eud in db.session.query(EUD).all():
            if not eud.uid or eud.uid == own_uid:
                continue
            cot = (db.session.query(CoT).filter(CoT.sender_uid == eud.uid, CoT.type.like('a-%'))
                   .order_by(CoT.timestamp.desc()).first())
            if cot and cot.xml and cot.stale:
                stale = cot.stale.replace(tzinfo=None) if cot.stale.tzinfo is None else \
                    cot.stale.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                if stale > now:
                    result.append(cot.xml)
    return result


def install_latest_positions(handler_class, positions=latest_positions):
    """A device that just connected gets everyone's last known position at once, as
    TAK Server does; upstream waits for each device's next report, so others show
    grey (or not at all) for up to a few minutes."""
    original = handler_class.parse_device_info
    if getattr(original, '_portabletak_positions', False):
        return False

    def parse_device_info(self, event):
        result = original(self, event)
        uid = getattr(self, 'uid', None)
        if uid and not getattr(self, '_portabletak_positions_sent', False):
            self._portabletak_positions_sent = True
            try:
                for xml in positions(self.app, uid):
                    self.request.send(xml.encode())
            except Exception as error:
                self.logger.warning('PortableTAK: could not send last positions: {}'.format(error))
        return result

    parse_device_info._portabletak_positions = True
    handler_class.parse_device_info = parse_device_info
    return True


def install_case_insensitive_users(datastore_class):
    """Usernames are case-insensitive in PortableTAK (phone keyboards capitalize the
    first letter: an iPhone enrolled as "Qrtest" for the user "qrtest" must connect)."""
    original = datastore_class.find_user
    if getattr(original, '_portabletak_case_insensitive', False):
        return False

    def find_user(self, case_insensitive=False, **kwargs):
        if kwargs.get('username'):
            case_insensitive = True
        return original(self, case_insensitive=case_insensitive, **kwargs)

    find_user._portabletak_case_insensitive = True
    datastore_class.find_user = find_user
    return True


def main():
    if len(sys.argv) < 2:
        print('usage: eud_handler_launch.py ENTRY_POINT [args...]', file=sys.stderr)
        return 2
    from flask_security import SQLAlchemyUserDatastore
    install_case_insensitive_users(SQLAlchemyUserDatastore)
    from opentakserver.eud_handler.EudHandler import EudHandler
    install(EudHandler)
    try:
        for name in os.listdir(REGISTRY):
            unregister(os.path.join(REGISTRY, name))
    except FileNotFoundError:
        pass
    # OpenTAKServer with federation (PR #343) has its own duplicate handling: a
    # newer connection displaces the older one and close_connection is idempotent.
    if not hasattr(EudHandler, 'displace'):
        install_duplicate_guard(EudHandler)
    install_idle_timeout(EudHandler)
    install_latest_positions(EudHandler)
    sys.argv = sys.argv[1:]
    from opentakserver.eud_handler.eud_handler import main as eud_main
    return eud_main()


if __name__ == '__main__':
    sys.exit(main())
