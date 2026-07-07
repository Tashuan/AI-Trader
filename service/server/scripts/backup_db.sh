#!/bin/bash
# SQLite DB backup script — creates timestamped copies with retention.
# Usage: ./service/server/scripts/backup_db.sh
# Add to cron: */15 * * * * /Users/tashuanspence/Development/ai-trader/service/server/scripts/backup_db.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DB_PATH="${1:-$SCRIPT_DIR/../data/clawtrader.db}"
BACKUP_DIR="$SCRIPT_DIR/../data/backups"
RETENTION_HOURS=48          # keep backups from last 48 hours
MAX_BACKUPS=200             # hard cap on number of backup files

if [ ! -f "$DB_PATH" ]; then
  echo "[backup] DB not found at $DB_PATH — skipping"
  exit 0
fi

mkdir -p "$BACKUP_DIR"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_FILE="$BACKUP_DIR/clawtrader_${TIMESTAMP}.db"

# Use sqlite3 .backup for a safe, consistent snapshot (handles WAL correctly)
sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"

if [ ! -f "$BACKUP_FILE" ]; then
  echo "[backup] Failed to create backup"
  exit 1
fi

# Compress to save space
gzip -f "$BACKUP_FILE"

SIZE=$(du -h "$BACKUP_FILE.gz" | cut -f1)
echo "[backup] Created $BACKUP_FILE.gz ($SIZE)"

# Prune old backups
find "$BACKUP_DIR" -name "clawtrader_*.db.gz" -mtime +$((RETENTION_HOURS / 24)) -delete 2>/dev/null || true

# Hard cap on count
BACKUP_COUNT=$(find "$BACKUP_DIR" -name "clawtrader_*.db.gz" | wc -l | tr -d ' ')
if [ "$BACKUP_COUNT" -gt "$MAX_BACKUPS" ]; then
  find "$BACKUP_DIR" -name "clawtrader_*.db.gz" -type f | sort | head -n $((BACKUP_COUNT - MAX_BACKUPS)) | xargs rm -f
  echo "[backup] Pruned to $MAX_BACKUPS files"
fi

echo "[backup] Done. Total backups: $(find "$BACKUP_DIR" -name 'clawtrader_*.db.gz' | wc -l | tr -d ' ')"
