#!/bin/sh
# Start the PortableTAK dashboard API in a test container (as a node's init script does).
cp -a /payload/usr/local/portabletak/. /usr/local/portabletak/
[ -d /e2e/ptapi ] || { echo API_LIBS_MISSING; exit 1; }
/usr/local/portabletak/bin/portabletak-users-worker stop >/dev/null 2>&1
pkill -f "uvicorn --app-dir /usr/local/portabletak/api" 2>/dev/null; sleep 1
PYTHONPATH=/e2e/ptapi PORTABLETAK_MANAGEMENT_AUTH=enforce setsid python3 -m uvicorn --app-dir /usr/local/portabletak/api app:app \
    --host 127.0.0.1 --port 8090 --proxy-headers --forwarded-allow-ips 127.0.0.1 >/tmp/api.log 2>&1 < /dev/null &
for i in $(seq 1 30); do wget -qO- http://127.0.0.1:8090/api/auth/session >/dev/null 2>&1 && { echo API=UP; exit 0; }; sleep 1; done
echo API=FAILED; tail -5 /tmp/api.log
