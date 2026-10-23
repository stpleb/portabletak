import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

LAUNCHER = (Path(__file__).resolve().parents[2] /
            'host-installer-20260929/payload/usr/local/portabletak/chroot/eud_handler_launch.py')
spec = importlib.util.spec_from_file_location('eud_handler_launch_files', LAUNCHER)
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)

COT = ('<event uid="x"><detail><fileshare filename="maps.zip" senderUrl="https://%s:8443/Marti/sync/content?hash=%s" '
       'sizeInBytes="10" sha256="%s"/></detail></event>') % (launch.FILE_HOST_PLACEHOLDER, 'a' * 64, 'a' * 64)


class Sock:
    def __init__(self, local):
        self.local = local

    def getsockname(self):
        return (self.local, 8089)


class Handler:
    def __init__(self, local):
        self.request = Sock(local)
        self.sent = []

    def on_message(self, channel, deliver, properties, body):
        self.sent.append(json.loads(body)['cot'])


class FileHostTests(unittest.TestCase):
    def test_lan_phone_gets_the_address_it_used(self):
        launch.install_federation_file_host(Handler)
        handler = Handler('192.168.1.193')
        handler.on_message(None, None, None, json.dumps({'uid': 'y', 'cot': COT}).encode())
        self.assertIn('https://192.168.1.193:8443/Marti/sync/content', handler.sent[0])
        self.assertNotIn(launch.FILE_HOST_PLACEHOLDER, handler.sent[0])

    def test_tailscale_phone_gets_the_drive_tailscale_address(self):
        with tempfile.NamedTemporaryFile('w', delete=False) as stream:
            stream.write('100.74.1.2\nportabletak-ab.tail1.ts.net\n')
        handler = Handler('127.0.0.1')
        self.assertEqual(launch.phone_host(handler, stream.name), '100.74.1.2')

    def test_other_messages_untouched_and_installed_once(self):
        launch.install_federation_file_host(Handler)
        launch.install_federation_file_host(Handler)
        handler = Handler('10.41.254.1')
        plain = json.dumps({'uid': 'y', 'cot': '<event uid="p"/>'}).encode()
        handler.on_message(None, None, None, plain)
        self.assertEqual(handler.sent, ['<event uid="p"/>'])


if __name__ == '__main__':
    unittest.main()
