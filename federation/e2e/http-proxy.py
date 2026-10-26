"""Test-only plain-HTTP front for a test drive's dashboard (https, self-signed), so the
desktop app's browser pane can open it:  python3 http-proxy.py PORT UPSTREAM_HOST:PORT
Rewrites Origin to the https form the dashboard expects and drops 'Secure' from cookies.
Never shipped on drives."""
import http.client
import ssl
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT, UPSTREAM = int(sys.argv[1]), sys.argv[2]
DEV_SECRETS = sys.argv[3] if len(sys.argv) > 3 else '/e2e/D/secrets/request.json'
HOST, UPORT = UPSTREAM.rsplit(':', 1)
CTX = ssl._create_unverified_context()
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization', 'te', 'trailers',
       'transfer-encoding', 'upgrade'}


class Proxy(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def handle_any(self):
        length = int(self.headers.get('Content-Length') or 0)
        body = self.rfile.read(length) if length else None
        if self.path.startswith('/__devlogin'):
            # Sign in with the dev drive's generated test password (never shown).
            import json
            password = json.load(open(DEV_SECRETS))['administrator_password']
            self.path, self.command = '/api/auth/login', 'POST'
            body = json.dumps({'username': 'administrator', 'password': password}).encode()
            self.headers.replace_header('Content-Type', 'application/json') if 'Content-Type' in self.headers else self.headers.add_header('Content-Type', 'application/json')
            self.headers.add_header('Origin', 'x')
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP and k.lower() != 'content-length'}
        if body is not None:
            headers['Content-Length'] = str(len(body))
        if 'Origin' in headers:
            headers['Origin'] = 'https://' + self.headers.get('Host', '')
        conn = http.client.HTTPSConnection(HOST, int(UPORT), context=CTX, timeout=300)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            data = resp.read()
        except OSError as error:
            self.send_error(502, str(error))
            return
        self.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() in HOP or k.lower() == 'content-length':
                continue
            if k.lower() == 'set-cookie':
                v = v.replace('; Secure', '')
            self.send_header(k, v)
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(data)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = handle_any

    def log_message(self, *args):
        pass


ThreadingHTTPServer(('0.0.0.0', PORT), Proxy).serve_forever()
