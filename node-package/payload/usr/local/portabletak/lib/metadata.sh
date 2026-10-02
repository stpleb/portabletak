#!/bin/sh

portabletak_metadata_get()
{
    FILE="$1"
    KEY="$2"

    [ -r "$FILE" ] || return 1

    VALUE="$(awk -v key="$KEY" '
        index($0, key "=") == 1 {
            print substr($0, length(key) + 2)
        }
    ' "$FILE")"

    [ -n "$VALUE" ] || return 1

    printf '%s\n' "$VALUE"
    return 0
}
