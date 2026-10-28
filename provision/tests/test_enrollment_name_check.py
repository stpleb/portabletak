"""Certificate enrollment refuses a CSR whose name is not the signed-in user
(opentakserver_launch.py). OTS's own packages (pyOpenSSL, bleach, PyJWT,
flask-security) are replaced by small stand-ins; the devmap container check
exercises the real ones."""
import base64
import importlib.util
from pathlib import Path
import sys
import types
import unittest

from flask import Flask, request

LAUNCHER = (Path(__file__).resolve().parents[2] /
            'host-installer-20260929/payload/usr/local/portabletak/chroot/opentakserver_launch.py')
spec = importlib.util.spec_from_file_location('opentakserver_launch_cn', LAUNCHER)
launch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launch)

PASSWORDS = {'alice': 'alice-pass', 'mallory': 'mallory-pass'}
TOKENS = {'alice-token': 'alice', 'mallory-token': 'mallory'}


class User:
    def __init__(self, username):
        self.username = username
        self.password = PASSWORDS[username]


class Datastore:
    def find_user(self, username=None, **kwargs):
        name = (username or '').lower()
        return User(name) if name in PASSWORDS else None


def stand_ins():
    bleach = types.ModuleType('bleach')
    bleach.clean = lambda text: text.replace('<', '&lt;')
    security = types.ModuleType('flask_security')
    security.verify_password = lambda password, stored: password == stored
    return {'bleach': bleach, 'flask_security': security}


def auth(username, password):
    return 'Basic ' + base64.b64encode(('%s:%s' % (username, password)).encode()).decode()


class EnrollmentNameCheck(unittest.TestCase):
    def setUp(self):
        self.saved = {name: sys.modules.get(name) for name in ('bleach', 'flask_security')}
        sys.modules.update(stand_ins())
        self.old = launch.csr_common_name, launch.token_subject
        launch.csr_common_name = lambda data: data.decode()     # the "CSR" is just its CN
        launch.token_subject = lambda app, token: TOKENS.get(token)
        self.app = Flask('ots')
        self.app.security = types.SimpleNamespace(datastore=Datastore())
        self.signed = []

        @self.app.route('/Marti/api/tls/signClient/v2', methods=['POST'],
                        endpoint='marti_blueprint.certificate_authority_api_blueprint.sign_csr_v2')
        def sign_csr_v2():
            self.signed.append(request.get_data().decode())
            return 'signed', 200

        self.logger = types.SimpleNamespace(warning=lambda *a: None)
        self.assertTrue(launch.install_enrollment_check(self.app, self.logger))
        self.client = self.app.test_client()

    def tearDown(self):
        launch.csr_common_name, launch.token_subject = self.old
        for name, module in self.saved.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    def enroll(self, header, cn):
        return self.client.post('/Marti/api/tls/signClient/v2?clientUid=ANDROID-1',
                                data=cn.encode(), headers={'Authorization': header}).status_code

    def test_own_name_is_signed(self):
        self.assertEqual(self.enroll(auth('alice', 'alice-pass'), 'alice'), 200)
        self.assertEqual(self.signed, ['alice'])

    def test_name_case_is_ignored(self):
        self.assertEqual(self.enroll(auth('Alice', 'alice-pass'), 'alice'), 200)
        self.assertEqual(self.enroll(auth('alice', 'alice-pass'), 'ALICE'), 200)

    def test_another_users_name_is_refused(self):
        self.assertEqual(self.enroll(auth('mallory', 'mallory-pass'), 'alice'), 403)
        self.assertEqual(self.signed, [])

    def test_qr_token_of_another_user_is_refused(self):
        # Upstream accepts any valid token whatever user name comes with it.
        self.assertEqual(self.enroll(auth('alice', 'mallory-token'), 'alice'), 403)
        self.assertEqual(self.signed, [])

    def test_own_qr_token_is_signed(self):
        self.assertEqual(self.enroll(auth('alice', 'alice-token'), 'alice'), 200)

    def test_ldap_checks_name_only(self):
        self.app.config['OTS_ENABLE_LDAP'] = True
        self.assertEqual(self.enroll(auth('carol', 'ldap-pass'), 'carol'), 200)
        self.assertEqual(self.enroll(auth('carol', 'ldap-pass'), 'alice'), 403)

    def test_bad_sign_in_is_left_to_upstream(self):
        # Upstream answers 401 itself (this stand-in signs); the check does not step in.
        self.assertEqual(self.enroll(auth('alice', 'wrong'), 'alice'), 200)
        self.assertEqual(self.enroll('', 'alice'), 200)

    def test_installed_once(self):
        endpoint = 'marti_blueprint.certificate_authority_api_blueprint.sign_csr_v2'
        wrapped = self.app.view_functions[endpoint]
        self.assertTrue(launch.install_enrollment_check(self.app, self.logger))
        self.assertIs(self.app.view_functions[endpoint], wrapped)

    def test_missing_view_is_reported(self):
        self.assertFalse(launch.install_enrollment_check(Flask('other'), self.logger))


if __name__ == '__main__':
    unittest.main()
