#!/bin/sh
# /certs holds ca.pem, server.pem, server.key (the test CA's). Starts Murmur and the sign-in check.
set -e
mkdir -p /data
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
exec python3 /auth/takptt_auth.py --ca /certs/ca.pem --ids /data/ids.json --secret-file /data/ice-secret ${LOGIN_URL:+--login-url "$LOGIN_URL"}
