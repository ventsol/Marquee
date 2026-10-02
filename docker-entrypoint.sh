#!/bin/sh
# Entrypoint: reconcile the mounted volume with the unprivileged user.
#
# Railway attaches volumes AFTER the image is built, which replaces /app/data
# with a root-owned mount. The chown in the Dockerfile cannot fix that - it ran
# at build time, before the mount existed. Without this, SQLite fails with
# "unable to open database file".
#
# Requires the container to start as root, so we drop privileges via su-exec.
set -e

DATA_DIR="${DATA_DIR:-/app/data}"

if [ "$(id -u)" = "0" ]; then
    # Ensure the mount exists and is writable by the runtime user.
    mkdir -p "$DATA_DIR"
    chown -R marquee:marquee "$DATA_DIR" 2>/dev/null || true
    chmod 755 "$DATA_DIR" 2>/dev/null || true

    exec su-exec marquee "$@"
fi

# Already non-root (e.g. a platform that forces a user): just run.
exec "$@"
