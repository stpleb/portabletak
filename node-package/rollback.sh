#!/bin/sh
set -eu

[ "$(id -u)" = 0 ] || { echo "root required" >&2; exit 10; }
[ "$#" = 1 ] || { echo "usage: rollback.sh BACKUP_ARCHIVE" >&2; exit 2; }
BACKUP="$1"
case "$BACKUP" in
    /usr/local/portabletak-backups/host-*.tar.gz) ;;
    *) echo "invalid backup path" >&2; exit 11 ;;
esac
[ -f "$BACKUP" ] || { echo "backup not found" >&2; exit 12; }
tar -tzf "$BACKUP" | awk '
    $0 !~ /^portabletak(\/|$)/ || $0 ~ /(^|\/)\.\.?(\/|$)/ { bad=1 }
    END { exit bad ? 1 : 0 }
' || { echo "invalid backup archive" >&2; exit 13; }
/etc/init.d/portabletak-api stop >/dev/null 2>&1 || true
tar -C /usr/local -xzf "$BACKUP"
/etc/init.d/portabletak-api start
echo "PORTABLETAK_ROLLBACK=COMPLETE"
