#!/bin/sh
# Container entrypoint. Set RUN_MIGRATIONS=true on exactly one replica / init job to apply database migrations.
set -eu
if [ "${RUN_MIGRATIONS:-false}" = "true" ]; then
    echo "Applying database migrations..."
    alembic upgrade head
fi
exec "$@"
