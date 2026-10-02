import io
from pathlib import Path
import sys
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
import client_packages as cp

RECORD = {'advertise': '10.41.254.1',
          'addresses': ['10.41.254.1', '10.41.250.10', '100.93.136.26', 'portabletak-a21f50bc.tailc13345.ts.net']}

ATAK_PREF = '''<?xml version='1.0' standalone='yes'?>
                <preferences>
                    <preference version="1" name="cot_streams">
                        <entry key="count" class="class java.lang.Integer">1</entry>
                        <entry key="description0" class="class java.lang.String">OpenTAKServer_10.41.254.1</entry>
                        <entry key="enabled0" class="class java.lang.Boolean">true</entry>
                        <entry key="connectString0" class="class java.lang.String">10.41.254.1:8089:ssl</entry>
                    </preference>
                    <preference version="1" name="com.atakmap.app_preferences">
                        <entry key="caPassword" class="class java.lang.String">SECRET</entry>
                    </preference>
                </preferences>
'''


def zip_of(members):
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w') as z:
        for name, data in members:
            z.writestr(name, data)
    return out.getvalue()


def atak_package():
    inner = zip_of([('u1/truststore-root.p12', b'\x01CA'), ('u1/preference.pref', ATAK_PREF),
                    ('u1/droid1.p12', b'\x02KEY')])
    return zip_of([('MANIFEST/manifest.xml', '<MissionPackageManifest/>'), ('u1/droid1.zip', inner)])


def streams(pref):
    import re
    body = re.search(r'name="cot_streams">(.*?)</preference>', pref, re.S).group(1)
    return dict(re.findall(r'key="(\w+)"[^>]*>([^<]*)<', body))


class ClientPackageTests(unittest.TestCase):
    def test_connections_order_labels_and_enabled(self):
        conns = cp.connections(RECORD)
        self.assertEqual([c[1] for c in conns], RECORD['addresses'])
        self.assertEqual([c[2] for c in conns], [True, False, True, False])
        self.assertEqual([c[0] for c in conns], ['PortableTAK (Wi-Fi)', 'PortableTAK (mesh)',
                                                 'PortableTAK (Tailscale)', 'PortableTAK (Tailscale name)'])

    def test_server_name_used_in_descriptions(self):
        conns = cp.connections(RECORD, name='Node02 TAK')
        self.assertEqual(conns[0][0], 'Node02 TAK (Wi-Fi)')
        self.assertEqual(conns[2][0], 'Node02 TAK (Tailscale)')
        self.assertEqual(cp.connections(RECORD, name='bad<name>')[0][0], 'PortableTAK (Wi-Fi)')
        other = cp.connections({'advertise': '10.41.254.1', 'addresses': ['10.41.254.1', '192.168.8.2']}, name='T')
        self.assertEqual([c[0] for c in other], ['T (Wi-Fi)', 'T (LAN 192.168.8.2)'])

    def test_advertised_goes_first_even_if_listed_later(self):
        conns = cp.connections({'advertise': '100.93.136.26', 'addresses': ['10.41.254.1', '100.93.136.26']})
        self.assertEqual([(c[1], c[2]) for c in conns], [('100.93.136.26', True), ('10.41.254.1', False)])

    def test_single_address_selection(self):
        self.assertEqual(cp.connections(RECORD, only='100.93.136.26'),
                         [('PortableTAK (Tailscale)', '100.93.136.26', True)])
        with self.assertRaises(ValueError):
            cp.connections(RECORD, only='10.9.9.9')

    def test_atak_package_rewritten_and_everything_else_kept(self):
        original = atak_package()
        result = cp.build(original, 'atak', RECORD)
        outer = zipfile.ZipFile(io.BytesIO(result))
        self.assertEqual(outer.namelist(), ['MANIFEST/manifest.xml', 'u1/droid1.zip'])
        self.assertEqual(outer.read('MANIFEST/manifest.xml'), b'<MissionPackageManifest/>')
        inner = zipfile.ZipFile(io.BytesIO(outer.read('u1/droid1.zip')))
        self.assertEqual(inner.read('u1/droid1.p12'), b'\x02KEY')
        self.assertEqual(inner.read('u1/truststore-root.p12'), b'\x01CA')
        pref = inner.read('u1/preference.pref').decode()
        s = streams(pref)
        self.assertEqual(s['count'], '4')
        self.assertEqual(s['connectString2'], '100.93.136.26:8089:ssl')
        self.assertEqual(s['enabled1'], 'false')
        self.assertIn('SECRET', pref)
        self.assertEqual(pref.count('name="cot_streams"'), 1)
        self.assertIn('\n                    </preference>\n                    <preference version="1" name="com.atakmap', pref)

    def test_itak_package(self):
        pref = ATAK_PREF.replace('                ', '')
        data = zip_of([('config.pref', pref), ('droid1.p12', b'K'), ('truststore-root.p12', b'C')])
        result = zipfile.ZipFile(io.BytesIO(cp.build(data, 'itak', RECORD)))
        self.assertEqual(streams(result.read('config.pref').decode())['count'], '4')
        self.assertEqual(result.read('droid1.p12'), b'K')

    def test_unexpected_layout_refused(self):
        with self.assertRaises(ValueError):
            cp.build(zip_of([('other.txt', 'x')]), 'itak', RECORD)
        with self.assertRaises(ValueError):
            cp.build(zip_of([('config.pref', '<preferences/>')]), 'itak', RECORD)

    def test_real_packages_if_present(self):
        root = Path(__file__).resolve().parents[2] / 'test-credentials'
        for name, kind in (('droid1-atak.zip', 'atak'), ('droid1-itak.zip', 'itak')):
            path = root / name
            if not path.is_file():
                continue
            data = cp.build(path.read_bytes(), kind, RECORD)
            outer = zipfile.ZipFile(io.BytesIO(data))
            self.assertIsNone(outer.testzip())


class TlsPackageTests(unittest.TestCase):
    def test_flat_sitx_layout_with_passwords_and_connections(self):
        def z(files):
            b = io.BytesIO()
            with zipfile.ZipFile(b, 'w') as f:
                for k, v in files.items():
                    f.writestr(k, v)
            return b.getvalue()
        pref = ('<preferences><preference version="1" name="cot_streams"><entry key="count">1</entry></preference>'
                '<preference version="1" name="com.atakmap.app_preferences">'
                '<entry key="caPassword" class="x">CAPW</entry><entry key="clientPassword" class="x">CLPW</entry>'
                '</preference></preferences>')
        inner = z({'u/preference.pref': pref, 'u/truststore-root.p12': b'T', 'u/skydio1.p12': b'C'})
        package = z({'MANIFEST/manifest.xml': 'm', 'x/skydio1.zip': inner})
        record = {'advertise': '10.41.254.181', 'addresses': ['10.41.254.181', '100.102.110.115']}
        out = zipfile.ZipFile(io.BytesIO(cp.tls_package(package, record, 'Team TAK', 'skydio1', uid='u-1')))
        self.assertEqual(sorted(out.namelist()), ['MANIFEST/manifest.xml', 'README.txt', 'secure.pref',
                                                  'skydio1.p12', 'truststore-root.p12'])
        self.assertEqual(out.read('skydio1.p12'), b'C')
        self.assertEqual(out.read('truststore-root.p12'), b'T')
        secure = out.read('secure.pref').decode()
        self.assertIn('>10.41.254.181:8089:ssl<', secure)
        self.assertIn('>100.102.110.115:8089:ssl<', secure)
        self.assertIn('key="caPassword" class="class java.lang.String">CAPW<', secure)
        self.assertIn('key="clientPassword" class="class java.lang.String">CLPW<', secure)
        self.assertIn('cert/skydio1.p12', secure)
        manifest = out.read('MANIFEST/manifest.xml').decode()
        for entry in ('truststore-root.p12', 'skydio1.p12', 'secure.pref'):
            self.assertIn('zipEntry="%s"' % entry, manifest)
        self.assertIn('Server name:  TeamTAK', out.read('README.txt').decode())

    def test_unexpected_layout_refused(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, 'w') as f:
            f.writestr('nothing.txt', 'x')
        with self.assertRaises(ValueError):
            cp.tls_package(buf.getvalue(), {'advertise': '10.0.0.1', 'addresses': ['10.0.0.1']}, 'T', 'u')


if __name__ == '__main__':
    unittest.main()


class TrustPackageTests(unittest.TestCase):
    def test_trust_package_is_flat_with_manifest_listing_ca_and_pref(self):
        data = cp.trust_package(b'P12DATA', 'pa<ss', 'Field Kit')
        z = zipfile.ZipFile(io.BytesIO(data))
        self.assertEqual(sorted(z.namelist()), ['MANIFEST/manifest.xml', 'trust.pref', 'truststore-root.p12'])
        manifest = z.read('MANIFEST/manifest.xml').decode()
        self.assertIn('Field_Kit_TRUST', manifest)
        self.assertIn('zipEntry="truststore-root.p12"', manifest)
        self.assertIn('zipEntry="trust.pref"', manifest)
        self.assertEqual(z.read('truststore-root.p12'), b'P12DATA')
        pref = z.read('trust.pref').decode()
        self.assertIn('pa&lt;ss', pref)
        self.assertIn('>cert/truststore-root.p12<', pref)
        self.assertNotIn('cot_streams', pref)
        self.assertNotIn('certificateLocation', pref)
