#!/bin/sh
# Adjust the runtime user to PUID/PGID (arr-stack convention), then drop
# privileges. When the container is started as non-root, this is a no-op.
set -e

PUID="${PUID:-1000}"
PGID="${PGID:-1000}"

if [ "$(id -u)" = "0" ]; then
    groupmod -o -g "$PGID" media
    usermod -o -u "$PUID" media
    # /config holds the DB and must be writable; media libraries stay untouched.
    chown -R media:media /config 2>/dev/null || true
    exec gosu media "$@"
fi

exec "$@"