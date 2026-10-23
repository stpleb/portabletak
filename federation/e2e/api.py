"""Test client for the PortableTAK dashboard API, acting like the browser:
   python3 api.py A|B METHOD PATH [JSON]   (logs in as administrator via https://127.0.0.1:8444)"""
import http.cookiejar, json, ssl, sys, urllib.request
who, method, path = sys.argv[1], sys.argv[2], sys.argv[3]
body = sys.argv[4] if len(sys.argv) > 4 else None
base = 'https://127.0.0.1:8444'
ctx = ssl.create_default_context(); ctx.check_hostname = False; ctx.verify_mode = ssl.CERT_NONE
jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=ctx), urllib.request.HTTPCookieProcessor(jar))
def req(m, p, data=None, headers=None):
    h = {'Origin': base, 'Content-Type': 'application/json'}; h.update(headers or {})
    r = urllib.request.Request(base + p, data=data.encode() if data is not None else None, method=m, headers=h)
    try:
        with opener.open(r, timeout=180) as resp:
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
password = json.load(open('/e2e/%s/secrets/request.json' % who))['administrator_password']
code, _ = req('POST', '/api/auth/login', json.dumps({'username': 'administrator', 'password': password}))
assert code == 200, ('login', code)
_, session = req('GET', '/api/auth/session')
csrf = json.loads(session)['csrf']
code, out = req(method, path, body, {'X-CSRF-Token': csrf})
print(code, out)
