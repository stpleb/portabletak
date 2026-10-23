#!/bin/bash
# Run TAK Server 5.8 in a container (no systemd) as a federation test partner.
cd /opt/tak
[ -f CoreConfig.xml ] || cp CoreConfig.example.xml CoreConfig.xml
command -v sudo >/dev/null || { printf '#!/bin/sh\nexec "$@"\n' > /usr/local/bin/sudo; chmod 755 /usr/local/bin/sudo; }
pg_lsclusters 2>/dev/null | grep -q "^18 *main" || pg_createcluster -d /var/lib/postgresql/18/data 18 main >/dev/null
pg_ctlcluster 18 main start
[ -e /var/lib/postgresql/18/data ] || ln -s main /var/lib/postgresql/18/data   # where TAK's setup looks in Docker
[ -f /opt/tak/.db-ready ] || { bash /opt/tak/db-utils/takserver-setup-db.sh > /tmp/setup-db.log 2>&1 && touch /opt/tak/.db-ready; pg_ctlcluster 18 main restart; }
cd /opt/tak/certs
if [ ! -f files/takserver.jks ]; then
    export STATE=LA CITY=BatonRouge ORGANIZATIONAL_UNIT=PortableTAKTest
    ./makeRootCa.sh --ca-name TAKTestCA > /tmp/ca.log 2>&1 < /dev/null
    yes | ./makeCert.sh server takserver > /tmp/cert.log 2>&1
    yes | ./makeCert.sh client admin > /dev/null 2>&1
fi
cd /opt/tak
grep -q 'enableFederation="true"' CoreConfig.xml || sed -i 's#<federation>#<federation enableFederation="true">#' CoreConfig.xml
export CONFIG_MAX_HEAP=384 MESSAGING_MAX_HEAP=1024 API_MAX_HEAP=768 PLUGIN_MANAGER_MAX_HEAP=128 RETENTION_MAX_HEAP=128
mkdir -p logs
nohup ./takserver-config.sh > logs/config-console.log 2>&1 &
sleep 40
nohup ./takserver-messaging.sh > logs/messaging-console.log 2>&1 &
nohup ./takserver-api.sh > logs/api-console.log 2>&1 &
echo TAK_ENTRY=STARTED
sleep infinity
