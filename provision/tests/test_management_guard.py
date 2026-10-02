import asyncio
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'lib'))
from management_auth import ManagementAuth
from management_guard import AuditLog, LoginThrottle, ManagementGuard, Revocations

PASSWORD = 'correct-horse-battery'


async def inner(scope, receive, send):
    await send({'type': 'http.response.start', 'status': 200, 'headers': []})
    await send({'type': 'http.response.body', 'body': b'inner'})


class Clock:
    def __init__(self):
        self.now = 1000

    def __call__(self):
        return self.now


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.clock = Clock()
        self.auth = ManagementAuth(root / 'credential.json', root / 'session-key.json', clock=self.clock)
        self.audit_path = root / 'audit.log'
        self.revocations = Revocations(root / 'revoked.json', clock=self.clock)
        self.throttle = LoginThrottle(per_client=3, overall=5, window=900, clock=self.clock)
        self.guard = self.make('enforce')

    def tearDown(self):
        self.temp.cleanup()

    def make(self, mode):
        return ManagementGuard(inner, self.auth, self.revocations,
                               AuditLog(self.audit_path, clock=self.clock), mode, self.throttle)

    def initialize(self):
        self.auth.initialize(PASSWORD, 'x' * 64)

    def request(self, method, path, body=None, headers=(), client='127.0.0.1', guard=None):
        raw = json.dumps(body).encode() if body is not None else b''
        scope = {'type': 'http', 'method': method, 'path': path, 'scheme': 'http',
                 'client': (client, 5000),
                 'headers': [(b'host', b'127.0.0.1:8090')] +
                            [(k.encode(), v.encode()) for k, v in headers]}
        messages = []

        async def receive():
            return {'type': 'http.request', 'body': raw, 'more_body': False}

        async def send(message):
            messages.append(message)
        asyncio.run((guard or self.guard)(scope, receive, send))
        start = messages[0]
        response = {'status': start['status'],
                    'headers': {k.decode(): v.decode() for k, v in start['headers']}}
        payload = messages[1]['body']
        response['body'] = payload if payload == b'inner' else json.loads(payload)
        return response

    def login(self, password=PASSWORD, client='127.0.0.1'):
        return self.request('POST', '/api/auth/login', {'username': 'administrator', 'password': password},
                            [('content-type', 'application/json')], client=client)

    def session(self):
        response = self.login()
        self.assertEqual(response['status'], 200)
        token = response['headers']['set-cookie'].split(';')[0]
        return [('cookie', token), ('x-csrf-token', response['body']['csrf'])]

    def events(self):
        return [json.loads(line)['event'] for line in self.audit_path.read_text().splitlines()]

    def test_off_mode_passes_everything_through(self):
        response = self.request('POST', '/api/stop', guard=self.make('off'))
        self.assertEqual(response['body'], b'inner')

    def test_setup_mode_allows_only_local_wizard(self):
        self.assertEqual(self.request('GET', '/api/provision/status')['body'], b'inner')
        self.assertEqual(self.request('POST', '/api/provision')['body'], b'inner')
        self.assertEqual(self.request('POST', '/api/stop')['status'], 503)
        self.assertEqual(self.request('GET', '/api/users')['status'], 503)
        self.assertEqual(self.request('GET', '/api/provision/status', client='10.41.0.5')['status'], 503)
        self.assertEqual(self.request('GET', '/api/provisionx')['status'], 503)

    def test_unauthenticated_api_refused_once_initialized(self):
        self.initialize()
        self.assertEqual(self.request('GET', '/api/users')['status'], 401)
        self.assertEqual(self.request('GET', '/api/provision/status')['status'], 401)
        self.assertEqual(self.request('GET', '/')['body'], b'inner')

    def test_login_sets_strict_http_only_cookie(self):
        self.initialize()
        response = self.login()
        cookie = response['headers']['set-cookie']
        for part in ('HttpOnly', 'SameSite=Strict', 'Path=/', 'Max-Age=28800'):
            self.assertIn(part, cookie)
        self.assertNotIn(PASSWORD, json.dumps(response['body']))
        self.assertEqual(self.events(), ['login_succeeded'])

    def test_session_allows_reads_and_csrf_guards_writes(self):
        self.initialize()
        headers = self.session()
        self.assertEqual(self.request('GET', '/api/users', headers=headers[:1])['body'], b'inner')
        self.assertEqual(self.request('POST', '/api/stop', headers=headers[:1])['status'], 403)
        wrong = [headers[0], ('x-csrf-token', 'A' * 43)]
        self.assertEqual(self.request('POST', '/api/stop', headers=wrong)['status'], 403)
        self.assertEqual(self.request('POST', '/api/stop', headers=headers)['body'], b'inner')
        cross = headers + [('origin', 'http://evil.example')]
        self.assertEqual(self.request('POST', '/api/stop', headers=cross)['status'], 403)
        self.assertIn('request_denied', self.events())

    def test_csrf_token_bound_to_its_session(self):
        self.initialize()
        first, second = self.session(), self.session()
        mixed = [first[0], second[1]]
        self.assertEqual(self.request('POST', '/api/stop', headers=mixed)['status'], 403)

    def test_wrong_password_and_username_rejected_and_audited(self):
        self.initialize()
        self.assertEqual(self.login('wrong-password-123')['status'], 401)
        response = self.request('POST', '/api/auth/login', {'username': 'admin', 'password': PASSWORD},
                                [('content-type', 'application/json')])
        self.assertEqual(response['status'], 401)
        self.assertEqual(self.events(), ['login_failed', 'login_failed'])
        self.assertNotIn(PASSWORD, self.audit_path.read_text())

    def test_throttle_per_client_then_global(self):
        self.initialize()
        for _ in range(3):
            self.login('wrong-password-123')
        response = self.login()
        self.assertEqual(response['status'], 429)
        self.assertIn('retry-after', response['headers'])
        self.assertEqual(self.login(client='10.0.0.2')['status'], 200)
        self.login('wrong-password-123', client='10.0.0.3')
        self.login('wrong-password-123', client='10.0.0.4')
        self.assertEqual(self.login(client='10.0.0.5')['status'], 429)
        self.clock.now += 901
        self.assertEqual(self.login()['status'], 200)

    def test_logout_revokes_that_session_only(self):
        self.initialize()
        first, second = self.session(), self.session()
        response = self.request('POST', '/api/auth/logout', headers=first)
        self.assertEqual(response['status'], 200)
        self.assertIn('Max-Age=0', response['headers']['set-cookie'])
        self.assertEqual(self.request('GET', '/api/users', headers=first[:1])['status'], 401)
        self.assertEqual(self.request('GET', '/api/users', headers=second[:1])['body'], b'inner')

    def test_logout_requires_csrf(self):
        self.initialize()
        headers = self.session()
        self.assertEqual(self.request('POST', '/api/auth/logout', headers=headers[:1])['status'], 403)

    def test_revoke_all_ends_every_session(self):
        self.initialize()
        first, second = self.session(), self.session()
        self.assertEqual(self.request('POST', '/api/auth/revoke-all', headers=first)['status'], 200)
        for headers in (first, second):
            self.assertEqual(self.request('GET', '/api/users', headers=headers[:1])['status'], 401)
        self.clock.now += 2
        self.assertEqual(self.request('GET', '/api/users', headers=self.session()[:1])['body'], b'inner')

    def test_expired_session_refused(self):
        self.initialize()
        headers = self.session()
        self.clock.now += 8 * 3600
        self.assertEqual(self.request('GET', '/api/users', headers=headers[:1])['status'], 401)

    def test_session_endpoint_reports_state(self):
        self.initialize()
        self.assertFalse(self.request('GET', '/api/auth/session')['body']['authenticated'])
        headers = self.session()
        body = self.request('GET', '/api/auth/session', headers=headers[:1])['body']
        self.assertTrue(body['authenticated'])
        self.assertEqual(body['csrf'], headers[1][1])

    def test_login_input_checked(self):
        self.initialize()
        self.assertEqual(self.request('POST', '/api/auth/login', {'username': 'administrator'},
                                      [('content-type', 'application/json')])['status'], 400)
        self.assertEqual(self.request('POST', '/api/auth/login', {'username': 'a', 'password': 'b'})['status'], 415)
        big = {'username': 'administrator', 'password': 'x' * 5000}
        self.assertEqual(self.request('POST', '/api/auth/login', big,
                                      [('content-type', 'application/json')])['status'], 400)
        cross = [('content-type', 'application/json'), ('origin', 'http://evil.example')]
        self.assertEqual(self.request('POST', '/api/auth/login',
                                      {'username': 'administrator', 'password': PASSWORD}, cross)['status'], 403)

    def test_login_before_initialization_is_unavailable(self):
        self.assertEqual(self.login()['status'], 503)

    def test_tampered_cookie_refused(self):
        self.initialize()
        cookie, csrf = self.session()
        tampered = [('cookie', cookie[1][:-2] + 'AA'), csrf]
        self.assertEqual(self.request('GET', '/api/users', headers=tampered[:1])['status'], 401)


if __name__ == '__main__':
    unittest.main()
