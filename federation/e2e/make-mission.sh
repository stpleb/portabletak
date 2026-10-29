#!/bin/sh
# make-mission.sh NAME CREATOR_UID FILE_TEXT : a Data Sync mission with one file, as the
# Marti API writes it (PUT /Marti/api/missions, /Marti/sync/upload, PUT .../contents).
ROOT="$(/usr/local/portabletak/lib/pt-rootfs /mnt/asgard)"
N="$(printf %s "$1" | sed "s/'/''/g")"; FILE="pt-note-$(date +%s).txt"
mkdir -p /mnt/asgard/opentak/data/missions
printf '%s\n' "$3" > "/mnt/asgard/opentak/data/missions/$FILE"
SHA="$(sha256sum "/mnt/asgard/opentak/data/missions/$FILE" | cut -c1-64)"; SIZE="$(wc -c < "/mnt/asgard/opentak/data/missions/$FILE")"
cat > "$ROOT/tmp/mk.sql" <<SQL
insert into euds (uid, callsign, last_status) values ('$2', 'PTPHONE', 'Connected') on conflict do nothing;
insert into missions (name, description, tool, "group", default_role, creator_uid, create_time, guid, password_protected, invite_only, expiration)
  values ('$N', 'made on PortableTAK', 'public', '__ANON__', 'MISSION_SUBSCRIBER', '$2', now(), gen_random_uuid()::text, false, false, -1);
insert into groups_missions (group_id, mission_name) values ((select id from groups where name = '__ANON__'), '$N');
insert into mission_roles ("clientUid", username, "createTime", role_type, mission_name) values ('$2', 'anonymous', now(), 'MISSION_OWNER', '$N');
insert into mission_changes ("isFederatedChange", change_type, mission_name, timestamp, creator_uid, server_time) values (false, 'CREATE_MISSION', '$N', now(), '$2', now());
insert into mission_content (keywords, mime_type, filename, submission_time, submitter, uid, creator_uid, hash, size, expiration)
  values ('[]', 'text/plain', '$FILE', now(), 'ptuser', gen_random_uuid()::text, '$2', '$SHA', $SIZE, -1);
insert into mission_content_mission (mission_content_id, mission_name) values ((select id from mission_content where hash = '$SHA'), '$N');
insert into mission_changes ("isFederatedChange", change_type, mission_name, timestamp, creator_uid, server_time, content_uid)
  values (false, 'ADD_CONTENT', '$N', now(), '$2', now(), (select uid from mission_content where hash = '$SHA'));
SQL
chmod 644 "$ROOT/tmp/mk.sql"
chroot "$ROOT" su postgres -s /bin/sh -c "psql -X -q -v ON_ERROR_STOP=1 -d ots -1 -f /tmp/mk.sql" && echo "MADE $1 file=$FILE sha=$SHA"
