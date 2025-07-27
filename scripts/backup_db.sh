#!/bin/bash
# Backup SQLite database to secondary node with integrity checks

set -euo pipefail

# Configuration from environment or defaults
DB_PATH="${DATABASE_PATH:-/app/data/conversations.db}"
BACKUP_HOST="${BACKUP_HOST:-phx-ai01}"
BACKUP_PATH="${BACKUP_PATH:-/mnt/backups/chatgpt}"
BACKUP_USER="${BACKUP_USER:-root}"
LOG_FILE="${LOG_FILE:-/var/log/chatgpt-backup.log}"

# Ensure log directory exists
mkdir -p "$(dirname "$LOG_FILE")"

# Logging function
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Error handler
error_exit() {
    log "ERROR: $1"
    exit 1
}

# Check if database exists
if [ ! -f "$DB_PATH" ]; then
    error_exit "Database not found: $DB_PATH"
fi

# Create backup filename with timestamp
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILENAME="conversations_${TIMESTAMP}.db"
TEMP_BACKUP="/tmp/${BACKUP_FILENAME}"

log "Starting backup of $DB_PATH"

# Create backup using SQLite's backup command for consistency
log "Creating consistent backup..."
sqlite3 "$DB_PATH" ".backup '$TEMP_BACKUP'" || error_exit "SQLite backup failed"

# Verify backup integrity
log "Verifying backup integrity..."
sqlite3 "$TEMP_BACKUP" "PRAGMA integrity_check;" > /dev/null || error_exit "Backup integrity check failed"

# Calculate checksums
log "Calculating checksums..."
ORIGINAL_CHECKSUM=$(sha256sum "$DB_PATH" | awk '{print $1}')
BACKUP_CHECKSUM=$(sha256sum "$TEMP_BACKUP" | awk '{print $1}')

log "Original checksum: $ORIGINAL_CHECKSUM"
log "Backup checksum: $BACKUP_CHECKSUM"

# Get file sizes
ORIGINAL_SIZE=$(stat -c%s "$DB_PATH")
BACKUP_SIZE=$(stat -c%s "$TEMP_BACKUP")

log "Original size: $ORIGINAL_SIZE bytes"
log "Backup size: $BACKUP_SIZE bytes"

# Create remote backup directory if it doesn't exist
log "Creating remote directory..."
ssh "${BACKUP_USER}@${BACKUP_HOST}" "mkdir -p '${BACKUP_PATH}'" || error_exit "Failed to create remote directory"

# Transfer backup to remote host
log "Transferring backup to ${BACKUP_HOST}:${BACKUP_PATH}..."
rsync -avz --progress "$TEMP_BACKUP" \
    "${BACKUP_USER}@${BACKUP_HOST}:${BACKUP_PATH}/${BACKUP_FILENAME}" || error_exit "Rsync failed"

# Create checksum file on remote
echo "$BACKUP_CHECKSUM  $BACKUP_FILENAME" | \
    ssh "${BACKUP_USER}@${BACKUP_HOST}" "cat > '${BACKUP_PATH}/${BACKUP_FILENAME}.sha256'"

# Verify remote backup
log "Verifying remote backup..."
REMOTE_CHECKSUM=$(ssh "${BACKUP_USER}@${BACKUP_HOST}" \
    "sha256sum '${BACKUP_PATH}/${BACKUP_FILENAME}'" | awk '{print $1}')

if [ "$BACKUP_CHECKSUM" != "$REMOTE_CHECKSUM" ]; then
    error_exit "Remote backup verification failed! Checksums don't match"
fi

# Create latest symlink
ssh "${BACKUP_USER}@${BACKUP_HOST}" \
    "cd '${BACKUP_PATH}' && ln -sf '${BACKUP_FILENAME}' latest.db"

# Clean up old backups (keep last 7 days)
log "Cleaning up old backups..."
ssh "${BACKUP_USER}@${BACKUP_HOST}" \
    "find '${BACKUP_PATH}' -name 'conversations_*.db' -mtime +7 -delete"

# Remove temporary backup
rm -f "$TEMP_BACKUP"

# Log success
log "Backup completed successfully!"
log "Remote backup: ${BACKUP_HOST}:${BACKUP_PATH}/${BACKUP_FILENAME}"

# Write status file for monitoring
STATUS_FILE="/tmp/chatgpt-backup-status"
cat > "$STATUS_FILE" << EOF
last_backup_time=$TIMESTAMP
last_backup_size=$BACKUP_SIZE
last_backup_checksum=$BACKUP_CHECKSUM
last_backup_status=success
EOF

exit 0