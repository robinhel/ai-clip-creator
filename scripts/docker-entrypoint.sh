#!/bin/sh
# Docker creates a missing bind-mounted folder (work/, out/, klippdata/, data/) as root.
# Hand such a folder to the clipsmith user, then run the command as clipsmith, never as root.
set -e

if [ "$(id -u)" = "0" ]; then
    for dir in /app/work /app/out /app/klippdata /app/data /app/profiles /app/overlays; do
        mkdir -p "$dir"
        if [ "$(stat -c %u "$dir")" = "0" ]; then
            chown clipsmith:clipsmith "$dir"
        fi
    done
    exec setpriv --reuid=clipsmith --regid=clipsmith --init-groups "$@"
fi

exec "$@"
