#!/bin/sh
# Runs in the takptt-murmur-test image: Murmur, the sign-in check, the real recorder and the TLS front.
set -e
apt-get update -qq >/dev/null && apt-get install -y -qq --no-install-recommends python3-cryptography >/dev/null
mkdir -p /data /rec
head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n' > /data/ice-secret
cat > /data/murmur.ini <<INI
database=/data/murmur.sqlite
port=64738
ice="tcp -h 127.0.0.1 -p 6502"
icesecretwrite=$(cat /data/ice-secret)
sslCert=/certs/server.pem
sslKey=/certs/server.key
sslCA=/certs/ca.pem
welcometext=
users=100
INI
chown -R mumble-server: /data
mumble-server -fg -ini /data/murmur.ini &
sleep 3
python3 /auth/takptt_auth.py --ca /certs/ca.pem --ids /data/ids.json --secret-file /data/ice-secret --recorder /rec/recorder.sha1 &
sleep 4
python3 /recorder/takptt_recorder.py --data /rec --ca /certs/ca.pem --http 18096 &
exec python3 /tools/history-front.py /certs/server.pem /certs/server.key /certs/ca.pem 18096
