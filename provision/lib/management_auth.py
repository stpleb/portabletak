"""Password verification and signed sessions for PortableTAK management.

This module performs no network binding. The API layer must still enforce HTTPS,
same-origin requests, CSRF protection, and login rate limits before LAN or WAN
management is enabled.
"""

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import time

from portabletak_provision import ProvisionError, read_json, write_json


FORMAT = 1
USERNAME = 'administrator'
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_MAXMEM = 64 * 1024 * 1024
MAX_TOKEN = 2048
MAX_SESSION_SECONDS = 8 * 60 * 60


def _b64encode(value):
    return base64.urlsafe_b64encode(value).rstrip(b'=').decode('ascii')


def _b64decode(value):
    if not isinstance(value, str) or len(value) > MAX_TOKEN:
        raise ProvisionError('invalid_management_session')
    try:
        return base64.urlsafe_b64decode(value + '=' * (-len(value) % 4))
    except (ValueError, TypeError):
        raise ProvisionError('invalid_management_session') from None


def _password_digest(password, salt):
    if not isinstance(password, str) or not 8 <= len(password) <= 128:
        raise ProvisionError('invalid_management_password')
    return hashlib.scrypt(password.encode('utf-8'), salt=salt, n=SCRYPT_N,
                          r=SCRYPT_R, p=SCRYPT_P, maxmem=SCRYPT_MAXMEM,
                          dklen=32)


class ManagementAuth:
    def __init__(self, credential_path, session_key_path, clock=None):
        self.credential_path = Path(credential_path)
        self.session_key_path = Path(session_key_path)
        self.clock = clock or (lambda: int(time.time()))

    def initialize(self, password, session_secret):
        if not isinstance(session_secret, str) or len(session_secret) < 32:
            raise ProvisionError('invalid_management_session_secret')
        self.credential_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.session_key_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.credential_path.exists() or self.session_key_path.exists():
            if not (self.credential_path.is_file() and self.session_key_path.is_file()
                    and self.verify_password(USERNAME, password)):
                raise ProvisionError('management_auth_already_initialized')
            return {'initialized': True, 'existing': True, 'username': USERNAME}

        salt = secrets.token_bytes(16)
        digest = _password_digest(password, salt)
        credential = {
            'format': FORMAT,
            'username': USERNAME,
            'algorithm': 'scrypt',
            'n': SCRYPT_N,
            'r': SCRYPT_R,
            'p': SCRYPT_P,
            'salt': salt.hex(),
            'digest': digest.hex(),
        }
        session_key = hmac.new(session_secret.encode('utf-8'),
                               b'PortableTAK management session v1',
                               hashlib.sha256).hexdigest()
        write_json(self.credential_path, credential)
        write_json(self.session_key_path, {'format': FORMAT, 'key': session_key})
        os.chmod(self.credential_path, 0o600)
        os.chmod(self.session_key_path, 0o600)
        return {'initialized': True, 'existing': False, 'username': USERNAME}

    def _credential(self):
        data = read_json(self.credential_path)
        expected = {'format', 'username', 'algorithm', 'n', 'r', 'p', 'salt', 'digest'}
        if (set(data) != expected or data.get('format') != FORMAT
                or data.get('username') != USERNAME or data.get('algorithm') != 'scrypt'
                or data.get('n') != SCRYPT_N or data.get('r') != SCRYPT_R
                or data.get('p') != SCRYPT_P):
            raise ProvisionError('invalid_management_credentials')
        try:
            salt = bytes.fromhex(data['salt'])
            digest = bytes.fromhex(data['digest'])
        except (TypeError, ValueError):
            raise ProvisionError('invalid_management_credentials') from None
        if len(salt) != 16 or len(digest) != 32:
            raise ProvisionError('invalid_management_credentials')
        return data, salt, digest

    def _session_key(self):
        data = read_json(self.session_key_path)
        if set(data) != {'format', 'key'} or data.get('format') != FORMAT:
            raise ProvisionError('invalid_management_session_key')
        try:
            key = bytes.fromhex(data['key'])
        except (TypeError, ValueError):
            raise ProvisionError('invalid_management_session_key') from None
        if len(key) != 32:
            raise ProvisionError('invalid_management_session_key')
        return key

    def verify_password(self, username, password):
        data, salt, expected = self._credential()
        if not isinstance(username, str) or not hmac.compare_digest(username, data['username']):
            candidate = password if isinstance(password, str) and 8 <= len(password) <= 128 else 'x' * 12
            _password_digest(candidate, salt)
            return False
        try:
            actual = _password_digest(password, salt)
        except ProvisionError:
            return False
        return hmac.compare_digest(actual, expected)

    def create_session(self, username, ttl=MAX_SESSION_SECONDS):
        if username != USERNAME or not isinstance(ttl, int) or not 60 <= ttl <= MAX_SESSION_SECONDS:
            raise ProvisionError('invalid_management_session_request')
        now = self.clock()
        payload = {'format': FORMAT, 'username': username, 'issued_at': now,
                   'expires_at': now + ttl, 'nonce': secrets.token_hex(16)}
        encoded = _b64encode(json.dumps(payload, sort_keys=True,
                                        separators=(',', ':')).encode('utf-8'))
        signature = _b64encode(hmac.new(self._session_key(), encoded.encode('ascii'),
                                        hashlib.sha256).digest())
        return encoded + '.' + signature

    def verify_session(self, token):
        if not isinstance(token, str) or len(token) > MAX_TOKEN or token.count('.') != 1:
            raise ProvisionError('invalid_management_session')
        encoded, supplied = token.split('.', 1)
        expected = _b64encode(hmac.new(self._session_key(), encoded.encode('ascii'),
                                       hashlib.sha256).digest())
        if not hmac.compare_digest(supplied, expected):
            raise ProvisionError('invalid_management_session')
        try:
            payload = json.loads(_b64decode(encoded))
        except (UnicodeError, ValueError, json.JSONDecodeError):
            raise ProvisionError('invalid_management_session') from None
        if (not isinstance(payload, dict) or set(payload) != {
                'format', 'username', 'issued_at', 'expires_at', 'nonce'}
                or payload.get('format') != FORMAT or payload.get('username') != USERNAME
                or not isinstance(payload.get('issued_at'), int)
                or not isinstance(payload.get('expires_at'), int)
                or not isinstance(payload.get('nonce'), str)
                or len(payload['nonce']) != 32
                or payload['issued_at'] > self.clock() + 60
                or payload['expires_at'] <= self.clock()
                or payload['expires_at'] - payload['issued_at'] > MAX_SESSION_SECONDS):
            raise ProvisionError('invalid_management_session')
        return {'username': USERNAME, 'expires_at': payload['expires_at'],
                'issued_at': payload['issued_at'], 'nonce': payload['nonce']}

    def csrf_token(self, token):
        """Per-session CSRF value. Only valid alongside the session it binds."""
        return _b64encode(hmac.new(self._session_key(), b'csrf\0' + token.encode('ascii'),
                                   hashlib.sha256).digest())
