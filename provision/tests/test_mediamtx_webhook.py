import http.server
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
import urllib.parse


SCRIPT = Path(__file__).resolve().parents[1] / 'vendor/mediamtx/ots-webhook.py'


class Handler(http.server.BaseHTTPRequestHandler):
    query = None

    def do_GET(self):
        type(self).query = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        self.send_response(204)
        self.end_headers()

    def log_message(self, *args):
        pass


class WebhookTests(unittest.TestCase):
    def test_token_and_event_are_forwarded_without_stdout(self):
        with tempfile.TemporaryDirectory() as directory:
            token = 'test-token-that-must-not-be-printed'
            token_file = Path(directory) / 'token'
            token_file.write_text(token)
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            env = os.environ.copy()
            env.update(PORTABLETAK_MEDIAMTX_TOKEN_FILE=str(token_file),
                       PORTABLETAK_OTS_WEBHOOK_URL='http://127.0.0.1:{}/hook'.format(server.server_port),
                       MTX_PATH='alpha', MTX_SOURCE_TYPE='publisher', MTX_SOURCE_ID='source-1',
                       RTSP_PORT='8554')
            try:
                result = subprocess.run([str(SCRIPT), 'ready'], env=env, text=True,
                                        capture_output=True, timeout=10)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertEqual(Handler.query['event'], ['ready'])
            self.assertEqual(Handler.query['token'], [token])
            self.assertEqual(Handler.query['path'], ['alpha'])

    def test_unknown_event_is_rejected_before_token_read(self):
        env = os.environ.copy()
        env['PORTABLETAK_MEDIAMTX_TOKEN_FILE'] = '/does/not/exist'
        result = subprocess.run([str(SCRIPT), 'unknown'], env=env, text=True,
                                capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 3)
        self.assertIn('unsupported_event', result.stderr)


if __name__ == '__main__':
    unittest.main()
