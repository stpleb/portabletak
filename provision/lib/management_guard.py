"""ASGI guard for the management API: login, sessions, CSRF, throttle, audit.

Pure ASGI with no web-framework imports, so it wraps the existing FastAPI app:

    app = ManagementGuard(app, **guard_settings())

Mode 'off' (the default) passes every request through unchanged and only
serves /api/auth/*. Mode 'enforce' requires a valid session for every
/api/ route and a matching X-CSRF-Token for every state-changing request.
This guard does not provide transport security; keep the API on 127.0.0.1
until it sits behind TLS.
"""
import asyncio
from collections import deque
import hmac
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import time

from management_auth import MAX_SESSION_SECONDS, USERNAME, ManagementAuth
from portabletak_provision import ProvisionError, read_json, write_json

COOKIE = 'ptak_session'
UNSAFE = frozenset({'POST', 'PUT', 'PATCH', 'DELETE'})
PUBLIC = frozenset({('GET', '/'), ('GET', '/auth.js'), ('GET', '/remote-access.js'), ('GET', '/factory-reset.js'), ('GET', '/wizard.html'),
                    ('GET', '/provision.js')})
MAX_LOGIN_BODY = 4096
MAX_REVOKED = 10000
MAX_AUDIT_BYTES = 1024 * 1024
LOOPBACK = frozenset({'127.0.0.1', '::1'})
# Before first-run creates the management credential, only the local setup
# wizard may act. Everything else stays closed.
SETUP_PATHS = frozenset({'/api/status', '/api/provision'})


class LoginThrottle:
    """Per-client and global failure windows, held in memory."""

    def __init__(self, per_client=5, overall=20, window=900, clock=time.monotonic):
        self.per_client, self.overall, self.window, self.clock = per_client, overall, window, clock
        self.failures = {}
        self.all = deque()

    def _trim(self, queue, now):
        while queue and queue[0] <= now - self.window:
            queue.popleft()

    def retry_after(self, client):
        now = self.clock()
        self._trim(self.all, now)
        mine = self.failures.get(client, deque())
        self._trim(mine, now)
        waits = [int(q[0] + self.window - now) + 1 for q, limit in
                 ((mine, self.per_client), (self.all, self.overall)) if len(q) >= limit]
        return max(waits) if waits else 0

    def failed(self, client):
        now = self.clock()
        self.failures.setdefault(client, deque()).append(now)
        self.all.append(now)
        if len(self.failures) > 4096:
            self.failures.pop(next(iter(self.failures)))

    def succeeded(self, client):
        self.failures.pop(client, None)


class Revocations:
    """Revoked session nonces plus a not-before time for 'sign out everywhere'."""

    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.clock = clock or (lambda: int(time.time()))

    def _load(self):
        if not self.path.exists():
            return {'format': 1, 'not_before': 0, 'revoked': {}}
        data = read_json(self.path)
        if (set(data) != {'format', 'not_before', 'revoked'} or data['format'] != 1
                or not isinstance(data['revoked'], dict)):
            raise ProvisionError('invalid_revocation_list')
        return data

    def _save(self, data):
        now = self.clock()
        live = {n: e for n, e in data['revoked'].items() if isinstance(e, int) and e > now}
        data['revoked'] = dict(sorted(live.items(), key=lambda item: item[1])[-MAX_REVOKED:])
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        write_json(self.path, data)

    def revoke(self, session):
        data = self._load()
        data['revoked'][session['nonce']] = session['expires_at']
        self._save(data)

    def revoke_all(self):
        data = self._load()
        data['not_before'] = self.clock() + 1
        self._save(data)

    def is_revoked(self, session):
        data = self._load()
        return session['nonce'] in data['revoked'] or session['issued_at'] < data['not_before']


class AuditLog:
    def __init__(self, path, clock=None):
        self.path = Path(path)
        self.clock = clock or (lambda: int(time.time()))

    def record(self, event, client, **fields):
        entry = {'at': self.clock(), 'event': event, 'client': client}
        entry.update(fields)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            if self.path.stat().st_size > MAX_AUDIT_BYTES:
                os.replace(self.path, self.path.with_name(self.path.name + '.1'))
        except FileNotFoundError:
            pass
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'a') as stream:
            stream.write(json.dumps(entry, sort_keys=True) + '\n')


def guard_settings(root=None):
    """Settings from the environment. Credentials live with the cartridge."""
    root = Path(root or os.environ.get('PORTABLETAK_MANAGEMENT_DIR',
                                       '/mnt/asgard/config/management'))
    return {
        'auth': ManagementAuth(root / 'credential.json', root / 'session-key.json'),
        'revocations': Revocations(root / 'revoked-sessions.json'),
        'audit': AuditLog(root / 'audit.log'),
        'mode': os.environ.get('PORTABLETAK_MANAGEMENT_AUTH', 'off'),
    }


class ManagementGuard:
    def __init__(self, app, auth, revocations, audit, mode='off', throttle=None):
        if mode not in {'off', 'enforce'}:
            raise ValueError('invalid_management_auth_mode')
        self.app, self.auth, self.revocations, self.audit = app, auth, revocations, audit
        self.mode = mode
        self.throttle = throttle or LoginThrottle()

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path, method = scope['path'], scope['method']
        if path.startswith('/api/auth/'):
            return await self.auth_route(scope, receive, send, path, method)
        if self.mode == 'off' or (method, path) in PUBLIC:
            return await self.app(scope, receive, send)
        if not self.auth.credential_path.exists():
            if client_of(scope) in LOOPBACK and (path in SETUP_PATHS or path.startswith('/api/provision/')):
                return await self.app(scope, receive, send)
            return await respond(send, 503, {'detail': 'management_auth_not_initialized'})
        session, token, denied = self.check(scope, method)
        if denied:
            if denied[0] == 403:
                self.audit.record('request_denied', client_of(scope), path=path, reason=denied[1])
            return await respond(send, denied[0], {'detail': denied[1]})
        return await self.app(scope, receive, send)

    def session_of(self, scope):
        token = read_cookie(scope)
        if not token:
            return None, None
        try:
            session = self.auth.verify_session(token)
            if self.revocations.is_revoked(session):
                return None, None
        except ProvisionError:
            return None, None
        return session, token

    def check(self, scope, method):
        """Return (session, token, None) or (None, None, (status, reason))."""
        session, token = self.session_of(scope)
        if session is None:
            return None, None, (401, 'authentication_required')
        if method in UNSAFE:
            if not same_origin(scope):
                return None, None, (403, 'same_origin_required')
            supplied = header(scope, 'x-csrf-token')
            if not supplied or not hmac.compare_digest(supplied, self.auth.csrf_token(token)):
                return None, None, (403, 'csrf_token_required')
        return session, token, None

    async def auth_route(self, scope, receive, send, path, method):
        client = client_of(scope)
        if (method, path) == ('GET', '/api/auth/session'):
            session, token = self.session_of(scope)
            body = {'authenticated': session is not None, 'mode': self.mode}
            if session:
                body.update(csrf=self.auth.csrf_token(token), expires_at=session['expires_at'],
                            username=session['username'])
            return await respond(send, 200, body)
        if (method, path) == ('POST', '/api/auth/login'):
            return await self.login(scope, receive, send, client)
        if (method, path) in {('POST', '/api/auth/logout'), ('POST', '/api/auth/revoke-all')}:
            session, token, denied = self.check(scope, method)
            if denied:
                return await respond(send, denied[0], {'detail': denied[1]})
            if path.endswith('logout'):
                self.revocations.revoke(session)
                self.audit.record('logout', client)
            else:
                self.revocations.revoke_all()
                self.audit.record('revoke_all', client)
            return await respond(send, 200, {'ok': True}, cookie=clear_cookie(scope))
        return await respond(send, 404, {'detail': 'not_found'})

    async def login(self, scope, receive, send, client):
        if not same_origin(scope):
            return await respond(send, 403, {'detail': 'same_origin_required'})
        if header(scope, 'content-type').split(';')[0].strip().lower() != 'application/json':
            return await respond(send, 415, {'detail': 'json_required'})
        wait = self.throttle.retry_after(client)
        if wait:
            self.audit.record('login_throttled', client)
            return await respond(send, 429, {'detail': 'too_many_attempts', 'retry_after': wait},
                                 extra=[(b'retry-after', str(wait).encode())])
        raw = await read_body(receive, MAX_LOGIN_BODY)
        try:
            data = json.loads(raw) if raw is not None else None
        except (ValueError, UnicodeError):
            data = None
        if (not isinstance(data, dict) or set(data) != {'username', 'password'}
                or not all(isinstance(v, str) for v in data.values())):
            return await respond(send, 400, {'detail': 'invalid_login_request'})
        try:
            valid = await asyncio.to_thread(self.auth.verify_password,
                                            data['username'], data['password'])
        except ProvisionError:
            return await respond(send, 503, {'detail': 'management_auth_not_initialized'})
        if not valid:
            self.throttle.failed(client)
            self.audit.record('login_failed', client)
            return await respond(send, 401, {'detail': 'invalid_credentials'})
        self.throttle.succeeded(client)
        token = self.auth.create_session(USERNAME)
        session = self.auth.verify_session(token)
        self.audit.record('login_succeeded', client)
        cookie = session_cookie(scope, token, MAX_SESSION_SECONDS)
        return await respond(send, 200, {'ok': True, 'csrf': self.auth.csrf_token(token),
                                         'expires_at': session['expires_at']}, cookie=cookie)


def header(scope, name):
    wanted = name.encode('latin-1')
    for key, value in scope.get('headers', []):
        if key.lower() == wanted:
            return value.decode('latin-1')
    return ''


def client_of(scope):
    client = scope.get('client')
    return client[0] if client else 'unknown'


def same_origin(scope):
    origin = header(scope, 'origin')
    if not origin:
        # Browsers send Origin on cross-site POSTs; its absence means a
        # same-origin navigation or a non-browser client on localhost.
        return True
    return origin == f"{scope.get('scheme', 'http')}://{header(scope, 'host')}"


def read_cookie(scope):
    raw = header(scope, 'cookie')
    if not raw or len(raw) > 8192:
        return None
    try:
        jar = SimpleCookie()
        jar.load(raw)
    except Exception:
        return None
    morsel = jar.get(COOKIE)
    return morsel.value if morsel else None


def session_cookie(scope, token, max_age):
    secure = '; Secure' if scope.get('scheme') == 'https' else ''
    return f'{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}{secure}'


def clear_cookie(scope):
    return session_cookie(scope, '', 0)


async def read_body(receive, limit):
    body = bytearray()
    while True:
        message = await receive()
        body.extend(message.get('body', b''))
        if len(body) > limit:
            return None
        if not message.get('more_body'):
            return bytes(body)


async def respond(send, status, body, cookie=None, extra=()):
    payload = json.dumps(body).encode()
    headers = [(b'content-type', b'application/json'),
               (b'content-length', str(len(payload)).encode()),
               (b'cache-control', b'no-store'), *extra]
    if cookie:
        headers.append((b'set-cookie', cookie.encode('latin-1')))
    await send({'type': 'http.response.start', 'status': status, 'headers': headers})
    await send({'type': 'http.response.body', 'body': payload})
