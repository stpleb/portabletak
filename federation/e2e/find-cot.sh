#!/bin/sh
# find-cot.sh UID : show stored CoT for UID on this server
ROOT="$(/usr/local/portabletak/lib/pt-rootfs /mnt/asgard)"
cat > "$ROOT/tmp/q.sql" <<SQL
select uid, (regexp_match(xml, 'callsign="([^"]*)"'))[1], left(xml, 60) from cot where uid = '$1';
SQL
chmod 644 "$ROOT/tmp/q.sql"
chroot "$ROOT" su postgres -s /bin/sh -c "psql -X -A -t -d ots -f /tmp/q.sql"
