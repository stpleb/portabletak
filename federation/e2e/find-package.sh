#!/bin/sh
# find-package.sh SHA256 : is the data package on this server, and the file-share CoT?
ROOT="$(/usr/local/portabletak/lib/pt-rootfs /mnt/asgard)"
F=/mnt/asgard/opentak/data/uploads/$1.zip
[ -f "$F" ] && echo "FILE $(sha256sum "$F" | cut -c1-64 | grep -qx "$1" && echo sha-ok || echo SHA-BAD) $(wc -c < "$F") bytes" || echo "FILE missing"
cat > "$ROOT/tmp/q2.sql" <<SQL
select 'ROW', filename, size, keywords from data_packages where hash = '$1';
select 'COT', (regexp_match(xml, 'senderUrl="([^"]*)"'))[1] from cot where uid = '$1-share';
SQL
chmod 644 "$ROOT/tmp/q2.sql"
chroot "$ROOT" su postgres -s /bin/sh -c "psql -X -A -t -d ots -f /tmp/q2.sql"
