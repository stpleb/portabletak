"""Test stand-in for PortableTAK's nginx: TLS on 8443 with client certificates, passing /voice/ to the
recorder with the same headers (see dashboard_site.py). The recorder and what it checks are the real ones."""
import http.client
import http.server
import ssl
import sys
import urllib.parse

CERT, KEY, CA, BACK = sys.argv[1:5]


class Front(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        der = self.connection.getpeercert(binary_form=True)
        headers = {}
        if der:
            headers = {'X-Ssl-Verify': 'SUCCESS', 'X-Ssl-Cert': urllib.parse.quote(ssl.DER_cert_to_PEM_cert(der), safe='')}
        back = http.client.HTTPConnection('127.0.0.1', int(BACK), timeout=20)
        back.request('GET', self.path[len('/voice'):], headers=headers)
        r = back.getresponse()
        body = r.read()
        self.send_response(r.status)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


server = http.server.ThreadingHTTPServer(('0.0.0.0', 8443), Front)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.load_cert_chain(CERT, KEY)
ctx.load_verify_locations(CA)
ctx.verify_mode = ssl.CERT_REQUIRED
server.socket = ctx.wrap_socket(server.socket, server_side=True)
server.serve_forever()
