#!/bin/bash
# The HISTORY tab's code against the real recorder (server/recorder), with no WinTAK and no Windows.
# Needs Docker and network (it adds one Python package to the test image), the .NET 8 SDK and .test-ca.
#   tools/run-history-e2e.sh [work dir]     DOTNET picks the dotnet to use
set -euo pipefail
here=$(cd "$(dirname "$0")/.." && pwd)
repo=$(cd "$here/.." && pwd)
work=${1:-$(mktemp -d)}
DOTNET=${DOTNET:-dotnet}
export DOTNET_CLI_TELEMETRY_OPTOUT=1
mkdir -p "$work/certs"
ca="$repo/.test-ca"
for u in alice rogue; do openssl pkcs12 -export -in "$ca/$u.pem" -inkey "$ca/$u.key" -name "$u" -passout pass:atakatak -out "$work/certs/$u.p12"; done
openssl pkcs12 -export -nokeys -in "$ca/ca.pem" -name ca -passout pass:atakatak -out "$work/certs/trust.p12"
"$DOTNET" build -c Release -v quiet --nologo -o "$work/bin" "$here/TakPtt.E2E/TakPtt.E2E.csproj"
cleanup() { docker rm -f takptt-e2e-history >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup
docker run -d --name takptt-e2e-history -p 127.0.0.1:64738:64738/tcp -p 127.0.0.1:18443:8443/tcp -v "$ca":/certs:ro \
  -v "$repo/server/recorder":/recorder:ro -v "$repo/server/murmur":/auth:ro -v "$here/tools":/tools:ro --entrypoint sh takptt-murmur-test /tools/history-server.sh >/dev/null
for _ in $(seq 1 120); do nc -z 127.0.0.1 18443 2>/dev/null && [ -n "$(docker logs takptt-e2e-history 2>&1 | grep -i 'clip API')" ] && break; sleep 1; done
sleep 8
status=0
"$DOTNET" "$work/bin/TakPtt.E2E.dll" history 127.0.0.1 64738 18443 "$work/certs" || status=$?
# Clearing the server: the exact command printed in README.md, run against the recorder's real store.
before=$(docker exec takptt-e2e-history sh -c 'find /rec/clips -name "*.opus" | wc -l')
docker exec takptt-e2e-history sh -c 'mkdir -p /asgard/opentak && ln -sfn /rec /asgard/opentak/voice'
docker exec takptt-e2e-history sh -c "$(grep '^python3 -c' "$here/README.md")"
after=$(docker exec takptt-e2e-history sh -c 'find /rec/clips -name "*.opus" | wc -l')
rows=$(docker exec takptt-e2e-history python3 -c "import sqlite3; print(sqlite3.connect('/rec/history.sqlite').execute('select count(*) from clips').fetchone()[0])")
if [ "$before" -gt 0 ] && [ "$after" -eq 0 ] && [ "$rows" -eq 0 ]; then echo "PASS server clear: $before clips gone"; else echo "FAIL server clear: $before, $after, $rows"; status=1; fi
docker logs takptt-e2e-history 2>&1 | grep -iE "recorder|clip [0-9]|TextMessage|denied" | tail -12
[ $status -eq 0 ] || docker logs takptt-e2e-history 2>&1 | tail -15
exit $status
