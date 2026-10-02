"""Exercise actual ASGI routes without opening a listening socket."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'api'))
from fastapi import FastAPI
import provision_routes

class FakeCartridge:
    def __init__(self, complete=False):
        self.complete = complete
    def status(self):
        return {'ok': True, 'state': 'existing' if self.complete else 'new',
                'complete': self.complete, 'can_provision': False}
    def timezone_root(self):
        return Path(__file__).parent / 'fixtures/zoneinfo'

class FakeSupervisor:
    def __init__(self, busy=False): self.busy = busy
    def status(self): return {'ok': True, 'job': 'running' if self.busy else 'idle'}
    def launch(self, request):
        if self.busy:
            raise provision_routes.ProvisionError('bootstrap_job_busy')
        return {'ok': True, 'job': 'running', 'pid': 1234, 'started_at': 100}

class APITests(unittest.IsolatedAsyncioTestCase):
    async def request(self, path, payload=None, headers=None, method='POST'):
        app = FastAPI()
        app.include_router(provision_routes.router)
        body = json.dumps(payload).encode() if payload is not None else b''
        base = {'host': '127.0.0.1:8090', 'content-type': 'application/json',
                'x-portabletak-setup': '1', 'origin': 'http://127.0.0.1:8090'}
        base.update(headers or {})
        scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
                 'method': method, 'scheme': 'http', 'path': path,
                 'raw_path': path.encode(), 'query_string': b'',
                 'headers': [(k.encode(), v.encode()) for k,v in base.items()],
                 'client': ('127.0.0.1', 50000), 'server': ('127.0.0.1', 8090)}
        messages = []
        async def receive():
            return {'type': 'http.request', 'body': body, 'more_body': False}
        async def send(message):
            messages.append(message)
        await app(scope, receive, send)
        code = next(m['status'] for m in messages if m['type'] == 'http.response.start')
        raw = b''.join(m.get('body', b'') for m in messages if m['type'] == 'http.response.body')
        return code, json.loads(raw)

    def payload(self):
        return {'instance_name': 'Test Team', 'timezone': 'Etc/UTC',
                'administrator_password': 'Test-admin-password-123',
                'client_password': 'Test-client-password-456'}

    async def test_valid_input_no_activation(self):
        with patch.object(provision_routes, 'controller', FakeCartridge()):
            code, body = await self.request('/api/provision/validate', self.payload())
        self.assertEqual(code, 200)
        self.assertFalse(body['activated'])
        self.assertNotIn('password', json.dumps(body))

    async def test_cross_origin_rejected(self):
        code, _ = await self.request('/api/provision/validate', self.payload(), {'origin': 'https://evil.invalid'})
        self.assertEqual(code, 403)

    async def test_dns_rebinding_host_rejected(self):
        code, _ = await self.request('/api/provision/validate', self.payload(), {'host': 'evil.invalid', 'origin': 'http://evil.invalid'})
        self.assertEqual(code, 403)

    async def test_missing_custom_header_rejected(self):
        code, _ = await self.request('/api/provision/validate', self.payload(), {'x-portabletak-setup': ''})
        self.assertEqual(code, 403)

    async def test_wrong_content_type_rejected(self):
        code, _ = await self.request('/api/provision/validate', self.payload(), {'content-type': 'text/plain'})
        self.assertEqual(code, 415)

    async def test_large_request_rejected(self):
        code, _ = await self.request('/api/provision/validate', {'data': 'x' * 20000})
        self.assertEqual(code, 413)

    async def test_validation_error_does_not_echo_password(self):
        payload = dict(self.payload(), instance_name='invalid\nname')
        with patch.object(provision_routes, 'controller', FakeCartridge()):
            code, body = await self.request('/api/provision/validate', payload)
        self.assertEqual(code, 400)
        self.assertNotIn(payload['administrator_password'], json.dumps(body))

    async def test_completed_cartridge_refused(self):
        with patch.object(provision_routes, 'controller', FakeCartridge(True)):
            code, body = await self.request('/api/provision', self.payload())
        self.assertEqual(code, 409)
        self.assertEqual(body['detail'], 'cartridge_already_initialized')

    async def test_bootstrap_job_launched_without_echoing_passwords(self):
        with patch.object(provision_routes, 'controller', FakeCartridge()), \
             patch.object(provision_routes, 'supervisor', FakeSupervisor()), \
             patch.object(provision_routes, 'activation_enabled', return_value=True):
            code, body = await self.request('/api/provision', self.payload())
        self.assertEqual(code, 200)
        self.assertEqual(body['job'], 'running')
        self.assertNotIn('password', json.dumps(body))

    async def test_running_bootstrap_job_refuses_second_launch(self):
        with patch.object(provision_routes, 'controller', FakeCartridge()), \
             patch.object(provision_routes, 'supervisor', FakeSupervisor(True)), \
             patch.object(provision_routes, 'activation_enabled', return_value=True):
            code, body = await self.request('/api/provision', self.payload())
        self.assertEqual(code, 409)
        self.assertEqual(body['detail'], 'bootstrap_job_busy')

    async def test_activation_remains_fail_closed_until_release_gate(self):
        with patch.object(provision_routes, 'controller', FakeCartridge()), \
             patch.object(provision_routes, 'supervisor', FakeSupervisor()), \
             patch.object(provision_routes, 'activation_enabled', return_value=False):
            code, body = await self.request('/api/provision', self.payload())
        self.assertEqual(code, 409)
        self.assertEqual(body['detail'], 'runtime_backend_not_enabled')

    async def test_completed_status(self):
        with patch.object(provision_routes, 'controller', FakeCartridge(True)), \
             patch.object(provision_routes, 'supervisor', FakeSupervisor()):
            code, body = await self.request('/api/provision/status', method='GET')
        self.assertEqual(code, 200)
        self.assertTrue(body['complete'])

if __name__ == '__main__':
    unittest.main()
