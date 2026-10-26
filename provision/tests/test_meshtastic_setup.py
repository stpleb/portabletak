"""Meshtastic link: RabbitMQ MQTT plugin, listener, radio-only user, OTS switch."""
import base64
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'host-installer-20260929/payload/usr/local/portabletak/lib'))
import meshtastic_setup as ms  # noqa: E402


def cartridge(tmp, config_text='OTS_ENABLE_MESHTASTIC: false\nOTS_LISTENER_PORT: 18081\n'):
    root = Path(tmp)
    etc = root / 'rootfs/etc/rabbitmq'
    etc.mkdir(parents=True)
    (etc / 'rabbitmq.conf').write_text('listeners.tcp.default = 127.0.0.1:5672\n')
    (etc / 'portabletak-definitions.json').write_text(json.dumps(
        {'users': [{'name': 'portabletak', 'password_hash': 'x', 'tags': ['administrator']}],
         'vhosts': [{'name': '/'}], 'permissions': [{'user': 'portabletak', 'vhost': '/'}]}))
    (root / 'opentak/data').mkdir(parents=True)
    (root / 'opentak/data/config.yml').write_text(config_text)
    return root


class MeshtasticSetupTests(unittest.TestCase):
    def test_on_then_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = cartridge(tmp)
            settings = ms.save(root, True)
            self.assertTrue(ms.apply(root))
            etc = root / 'rootfs/etc/rabbitmq'
            self.assertEqual((etc / 'enabled_plugins').read_text(), '[rabbitmq_mqtt].\n')
            conf = (etc / 'rabbitmq.conf').read_text()
            self.assertIn('mqtt.listeners.tcp.default = 1883', conf)
            self.assertIn('mqtt.allow_anonymous = false', conf)
            defs = json.loads((etc / 'portabletak-definitions.json').read_text())
            radio = [u for u in defs['users'] if u['name'] == 'meshtastic'][0]
            raw = base64.b64decode(radio['password_hash'])
            self.assertEqual(raw[4:], hashlib.sha256(raw[:4] + settings['password'].encode()).digest())
            self.assertIn({'user': 'portabletak', 'vhost': '/'}, defs['permissions'])
            perm = [p for p in defs['permissions'] if p['user'] == 'meshtastic'][0]
            self.assertNotIn('.*', perm['write'].replace('mqtt-subscription-.*', ''))   # topics only
            self.assertIn('OTS_ENABLE_MESHTASTIC: true', (root / 'opentak/data/config.yml').read_text())
            # Again: idempotent, password kept.
            ms.apply(root)
            self.assertEqual((etc / 'rabbitmq.conf').read_text().count('mqtt.listeners'), 1)
            self.assertEqual(ms.load(root)['password'], settings['password'])
            # Off: everything removed again.
            ms.save(root, False)
            self.assertFalse(ms.apply(root))
            self.assertFalse((etc / 'enabled_plugins').exists())
            self.assertNotIn('mqtt.', (etc / 'rabbitmq.conf').read_text())
            self.assertEqual([u['name'] for u in json.loads((etc / 'portabletak-definitions.json').read_text())['users']], ['portabletak'])
            self.assertIn('OTS_ENABLE_MESHTASTIC: false', (root / 'opentak/data/config.yml').read_text())

    def test_json_style_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = cartridge(tmp, '{\n  "OTS_ENABLE_MESHTASTIC": false,\n  "OTS_LISTENER_PORT": 18081\n}\n')
            ms.save(root, True)
            ms.apply(root)
            self.assertIn('"OTS_ENABLE_MESHTASTIC": true', (root / 'opentak/data/config.yml').read_text())

    def test_password_hash_format(self):
        digest = base64.b64decode(ms.password_hash('secret', salt=b'\x01\x02\x03\x04'))
        self.assertEqual(digest[:4], b'\x01\x02\x03\x04')
        self.assertEqual(len(digest), 36)


if __name__ == '__main__':
    unittest.main()
