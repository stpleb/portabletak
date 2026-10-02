import importlib.util
from pathlib import Path
import tempfile
import unittest

LAUNCHER = (Path(__file__).resolve().parents[2] /
            'host-installer-20260929/payload/usr/local/portabletak/chroot/eud_handler_launch.py')
spec = importlib.util.spec_from_file_location('eud_handler_launch', LAUNCHER)
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)


class Channel:
    def __init__(self):
        self.is_open, self.is_closing, self.is_closed = True, False, False

    def close(self):
        self.is_closed = True


class Socket:
    def __init__(self):
        self.closed = False

    def shutdown(self, how):
        pass

    def close(self):
        self.closed = True


class Logger:
    def info(self, *a):
        pass


def handler_class(log):
    class Handler:
        def __init__(self, uid):
            self._uid = uid
            self.uid = None
            self.rabbit_channel = Channel()
            self.request = Socket()
            self.shutdown = False
            self.logger = Logger()
            self.client_address = ('10.0.0.1', 1)

        def parse_device_info(self, event):
            self.uid = self._uid

        def close_connection(self):
            log.append(('upstream_close', self.uid))
    return Handler


class DuplicateGuardTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.log = []
        self.live = set()
        self.H = handler_class(self.log)
        self.assertTrue(launch.install_duplicate_guard(self.H, self.dir, alive=lambda pid: pid in self.live))
        self.assertFalse(launch.install_duplicate_guard(self.H, self.dir))

    def connect(self, uid, pid):
        # Each forked connection has its own pid; simulate by renaming its marker.
        h = self.H(uid)
        h.parse_device_info(None)
        path = Path(h._portabletak_registered)
        new = path.with_name(f'{launch._key(uid)}.{pid}')
        path.rename(new)
        h._portabletak_registered = str(new)
        self.live.add(pid)
        return h

    def test_single_connection_closes_upstream_way(self):
        a = self.connect('ANDROID-1', 101)
        a.close_connection()
        self.assertEqual(self.log, [('upstream_close', 'ANDROID-1')])

    def test_duplicate_connection_keeps_device_connected(self):
        wifi = self.connect('ANDROID-1', 101)
        vpn = self.connect('ANDROID-1', 102)
        wifi.close_connection()
        self.assertEqual(self.log, [])
        self.assertTrue(wifi.rabbit_channel.is_closed)
        self.assertTrue(wifi.request.closed)
        self.assertFalse(vpn.rabbit_channel.is_closed)
        self.live.discard(101)
        vpn.close_connection()
        self.assertEqual(self.log, [('upstream_close', 'ANDROID-1')])

    def test_dead_sibling_ignored_and_cleaned(self):
        a = self.connect('ANDROID-1', 101)
        self.connect('ANDROID-1', 102)
        self.live.discard(102)  # died without closing
        a.close_connection()
        self.assertEqual(self.log, [('upstream_close', 'ANDROID-1')])
        self.assertEqual(list(Path(self.dir).iterdir()), [])

    def test_other_devices_do_not_count(self):
        self.connect('IPHONE-1', 103)
        a = self.connect('ANDROID-1', 101)
        a.close_connection()
        self.assertEqual(self.log, [('upstream_close', 'ANDROID-1')])

    def test_unregistered_connection_uses_upstream(self):
        h = self.H('X')
        h.close_connection()
        self.assertEqual(self.log, [('upstream_close', None)])

    def test_odd_uid_is_hashed(self):
        self.assertTrue(launch._key('a/b c').startswith('h-'))
        self.assertEqual(launch._key('1F221FB8-D5D9'), '1F221FB8-D5D9')



class IdleTimeoutTests(unittest.TestCase):
    def test_silent_connection_times_out_and_keepalive_is_on(self):
        import socket

        class Request:
            def __init__(self):
                self.timeout, self.options = None, {}

            def settimeout(self, value):
                self.timeout = value

            def setsockopt(self, level, name, value):
                self.options[(level, name)] = value

        class Handler:
            def handle(self):
                return 'handled'

        self.assertTrue(launch.install_idle_timeout(Handler, idle=120))
        self.assertFalse(launch.install_idle_timeout(Handler))
        handler = Handler()
        handler.request = Request()
        self.assertEqual(handler.handle(), 'handled')
        self.assertEqual(handler.request.timeout, 120)
        self.assertEqual(handler.request.options[(socket.SOL_SOCKET, socket.SO_KEEPALIVE)], 1)

    def test_socket_errors_do_not_stop_the_handler(self):
        class Request:
            def settimeout(self, value):
                raise OSError('closed')

        class Handler:
            def handle(self):
                return 'handled'

        launch.install_idle_timeout(Handler)
        handler = Handler()
        handler.request = Request()
        self.assertEqual(handler.handle(), 'handled')


if __name__ == '__main__':
    unittest.main()
