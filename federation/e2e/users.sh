#!/bin/sh
# users.sh JSON : run a dashboard helper action (as the dashboard API does)
printf "%s" "$1" | /usr/local/portabletak/bin/portabletak-users
