#!/usr/bin/env bash
set -euo pipefail

# restore.sh - Restore a PostgreSQL backup into the running 'postgres' container.
#
# Usage:
#   ./restore.sh <backup_file>
#
# Requires: the 'postgres' container to be running (docker compose up), and
# the target database to already exist (created by docker-entrypoint on first
# init, or by a fresh `docker compose up` with the named volume created).

CONTAINER_NAME="postgres"
DB_USER="${POSTGRES_USER:-barq_app}"
DB_NAME="${POSTGRES_DB:-barq_tasks}"

if [ "${1:-}" == "" ]; then
    echo "ERROR: usage: ./restore.sh <backup_file>" >&2
    exit 1
fi

BACKUP_FILE="$1"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: backup file not found: $BACKUP_FILE" >&2
    exit 1
fi

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "ERROR: container '$CONTAINER_NAME' is not running. Start the stack first (docker compose up -d)." >&2
    exit 1
fi

running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME")"
if [ "$running" != "true" ]; then
    echo "ERROR: container '$CONTAINER_NAME' is not running (state: $running)." >&2
    exit 1
fi

echo "Restoring database '$DB_NAME' (user '$DB_USER') into container '$CONTAINER_NAME'..."
echo "Source file: $BACKUP_FILE"

if ! docker exec -i "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" < "$BACKUP_FILE" > /tmp/restore_output.log 2>&1; then
    echo "ERROR: restore failed. Output:" >&2
    cat /tmp/restore_output.log >&2
    exit 1
fi

# Verify restore actually worked: the records table should be queryable and non-empty.
RECORD_COUNT="$(docker exec "$CONTAINER_NAME" psql -U "$DB_USER" -d "$DB_NAME" -t -c "SELECT COUNT(*) FROM records;" | tr -d '[:space:]')"

if ! [[ "$RECORD_COUNT" =~ ^[0-9]+$ ]]; then
    echo "ERROR: could not verify record count after restore (got: '$RECORD_COUNT')." >&2
    exit 1
fi

echo "PASS: restore completed. 'records' table now contains $RECORD_COUNT row(s)."