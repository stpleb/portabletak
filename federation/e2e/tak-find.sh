#!/bin/sh
su postgres -c "psql -X -A -t -d cot -c \"select uid, servertime from cot_router where uid = '$1' order by id desc limit 1\""
