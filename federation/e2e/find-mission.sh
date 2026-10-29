#!/bin/sh
# find-mission.sh NAME : a Data Sync mission on this server (row, groups, files, tracks, changes)
ROOT="$(/usr/local/portabletak/lib/pt-rootfs /mnt/asgard)"
N="$(printf %s "$1" | sed "s/'/''/g")"
cat > "$ROOT/tmp/qm.sql" <<SQL
select 'MISSION', name, tool, creator_uid, description, "group" from missions where name = '$N';
select 'GROUP', g.name from groups_missions gm join groups g on g.id = gm.group_id where gm.mission_name = '$N';
select 'FILE', c.filename, c.hash, c.size, c.mime_type from mission_content c join mission_content_mission l on l.mission_content_id = c.id where l.mission_name = '$N';
select 'TRACK', uid, creator_uid, callsign, cot_type from mission_uids where mission_name = '$N';
select 'CHANGE', change_type, "isFederatedChange", coalesce(content_uid, mission_uid, '') from mission_changes where mission_name = '$N' order by id;
SQL
chmod 644 "$ROOT/tmp/qm.sql"
chroot "$ROOT" su postgres -s /bin/sh -c "psql -X -A -t -d ots -f /tmp/qm.sql"
