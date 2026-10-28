#!/bin/sh
# Run the bundle on a copy of the PortableTAK base package set (ubuntu:24.04).
set -e
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq --no-install-recommends ca-certificates python3 python3-venv \
    postgresql-16 rabbitmq-server nginx openssl tzdata iproute2 curl libmagic1 >/dev/null 2>&1
B=/opt/murmur; mkdir -p $B /data && tar -C $B -xzf /w/out/murmur-$(uname -m).tar.gz
L=$B/root/usr/lib/$(uname -m)-linux-gnu
export LD_LIBRARY_PATH=$L QT_PLUGIN_PATH=$L/qt5/plugins
echo "missing libs:"; ldd $B/root/usr/bin/mumble-server | grep "not found" || echo none
PYTHONPATH=$B/root/usr/lib/python3/dist-packages python3 -c "import Ice; print('Ice', Ice.stringVersion())"
printf 'database=/data/m.sqlite\nice="tcp -h 127.0.0.1 -p 6502"\nicesecretwrite=x\nuname=nobody\n' > /data/m.ini; chown nobody /data
$B/root/usr/bin/mumble-server -ini /data/m.ini -fg > /data/log 2>&1 & sleep 4; grep -E "<F>|<C>|Server listening|Ice" /data/log | head -6
