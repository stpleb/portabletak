#!/bin/sh
# tak-mission.sh NAME : a Data Sync mission in TAK Server's database (row, files, tracks)
N="$(printf %s "$1" | sed "s/'/''/g")"
cat > /tmp/tm.sql <<SQL
select 'MISSION', id, name, tool, creatoruid, description from mission where name = '$N';
select 'FILE', r.filename, r.hash, length(r.data) from mission_resource mr join resource r on r.hash = mr.resource_hash join mission m on m.id = mr.mission_id where m.name = '$N';
select 'TRACK', uid from mission_uid mu join mission m on m.id = mu.mission_id where m.name = '$N';
select 'CHANGE', change_type, coalesce(hash, uid, '') from mission_change where mission_name = '$N' order by id;
SQL
chmod 644 /tmp/tm.sql
su postgres -c "psql -X -A -t -d cot -f /tmp/tm.sql"
