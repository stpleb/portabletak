#!/usr/bin/env python3
"""Temporary loopback-only upstream download proxy for the isolated builder."""
import http.server
import select
import ssl
import socket
import socketserver
import urllib.request
from urllib.parse import urlsplit

HOSTS = {'ports.ubuntu.com', 'pypi.org', 'files.pythonhosted.org'}
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def do_CONNECT(self):
        host, sep, port = self.path.rpartition(':')
        if host not in HOSTS or port != '443':
            self.send_error(403)
            return
        try:
            upstream = socket.create_connection((host, 443), timeout=30)
        except OSError:
            self.send_error(502)
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.flush()
        with upstream:
            peers = [self.connection, upstream]
            while True:
                readable, _, _ = select.select(peers, [], [], 90)
                if not readable:
                    break
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    (upstream if source is self.connection else self.connection).sendall(data)
    def do_GET(self):
        url = urlsplit(self.path)
        if url.hostname != 'ports.ubuntu.com' or url.scheme != 'http' or url.port not in (None, 80) or url.username or url.password:
            self.send_error(403)
            return
        request = urllib.request.Request('https://ports.ubuntu.com' + url.path + ('?' + url.query if url.query else ''))
        try:
            with urllib.request.urlopen(request, timeout=60, context=ssl.create_default_context(cafile='/etc/ssl/cert.pem')) as response:
                self.send_response(response.status)
                for key in ('Content-Length', 'Content-Type', 'Last-Modified'):
                    if response.headers.get(key):
                        self.send_header(key, response.headers[key])
                self.end_headers()
                while True:
                    block = response.read(65536)
                    if not block:
                        break
                    self.wfile.write(block)
        except (OSError, urllib.error.URLError):
            self.close_connection = True

class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

if __name__ == '__main__':
    Server(('127.0.0.1', 18765), Handler).serve_forever()
