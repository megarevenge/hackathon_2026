#!/bin/sh
set -eu
storage_path="${STORAGE_DIR:-/app/storage}"
mkdir -p "$storage_path"
if [ "$(id -u)" = "0" ]; then
    chown appuser:appuser "$storage_path"
    exec gosu appuser "$@"
fi
exec "$@"
