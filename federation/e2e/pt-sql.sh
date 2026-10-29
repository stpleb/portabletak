#!/bin/sh
# pt-sql.sh 'SQL' : run SQL on this drive's OpenTAKServer database (test changes)
ROOT="$(/usr/local/portabletak/lib/pt-rootfs /mnt/asgard)"
printf '%s\n' "$1" > "$ROOT/tmp/pt.sql"; chmod 644 "$ROOT/tmp/pt.sql"
chroot "$ROOT" su postgres -s /bin/sh -c "psql -X -A -t -v ON_ERROR_STOP=1 -d ots -1 -f /tmp/pt.sql"
