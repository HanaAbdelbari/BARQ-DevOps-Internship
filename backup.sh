#!/usr/bin/env bash
set -euo pipefail

# backup.sh - Create a PostgreSQL backup from the running 'postgres' container.
#
# Usage:
#   ./backup.sh [output_file]
#
# Defaults:
#   output_file = ./backups/barq_tasks_<timestamp>.sql
#
# Requires: the 'postgres' container to be running (docker compose up).
# Reads DB name/user from the same env the app uses (config/app.env), falling
# back to the known defaults from docker-compose.yml if not set.

CONTAINER_NAME="postgres"
DB_USER="${POSTGRES_USER:-barq_app}"
DB_NAME="${POSTGRES_DB:-barq_tasks}"
BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
OUTPUT_FILE="${1:-${BACKUP_DIR}/barq_tasks_${TIMESTAMP}.sql}"

mkdir -p "$(dirname "$OUTPUT_FILE")"

if ! docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" >/dev/null 2>&1; then
    echo "ERROR: container '$CONTAINER_NAME' is not running. Start the stack first (docker compose up -d)." >&2
    exit 1
fi

running="$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME")"
if [ "$running" != "true" ]; then
    echo "ERROR: container '$CONTAINER_NAME' is not running (state: $running)." >&2
    exit 1
fi

echo "Backing up database '$DB_NAME' (user '$DB_USER') from container '$CONTAINER_NAME'..."
echo "Output file: $OUTPUT_FILE"

if ! docker exec "$CONTAINER_NAME" pg_dump -U "$DB_USER" -d "$DB_NAME" --clean --if-exists > "$OUTPUT_FILE"; then
    echo "ERROR: pg_dump failed." >&2
    rm -f "$OUTPUT_FILE"
    exit 1
fi

if [ ! -s "$OUTPUT_FILE" ]; then
    echo "ERROR: backup file is empty; treating as failure." >&2
    rm -f "$OUTPUT_FILE"
    exit 1
fi

LINE_COUNT="$(wc -l < "$OUTPUT_FILE")"
echo "PASS: backup written to $OUTPUT_FILE (${LINE_COUNT} lines)."
echo "$OUTPUT_FILE"