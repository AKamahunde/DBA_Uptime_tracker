#!/usr/bin/env bash
# ==============================================================================
# ccisprod-weekly-backup.sh (Appliance + DBTRAC Edition)
# Backs up:
#   - Postgres/PostGIS 17 DB (pg_dump custom format + roles/globals, no downtime)
#   - LDAP volumes (ccisprod_ldap-vol, ccisprod_ldap-slapd.d-vol)
#   - Stack config files (/home/techsupport/odkx-prod)
#
# Key Upgrades from Original:
#   1. Writes directly to the external Backup Appliance (APPLIANCE_MOUNT), NEVER
#      to the local CCIS server root '/' disk.
#   2. Verifies APPLIANCE_MOUNT resides on a non-root mounted filesystem before
#      writing a single byte (works for mount roots and subdirectories alike).
#   3. Automatically pushes backup status (Success/Failed, duration, size, path)
#      to DBTRAC (http://192.168.35.38:90/api/backup/ingest/).
#
# Install:  /home/techsupport/odkx-prod/scripts/ccisprod-weekly-backup.sh (chmod 750)
# Schedule: /etc/cron.d/ccisprod-backup (0 23 * * 6 root ...)
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION ---
APPLIANCE_MOUNT="${APPLIANCE_MOUNT:-/nfsdata/backup}"
BACKUP_ROOT="${APPLIANCE_MOUNT}/ccisprod"
STACK_DIR="/home/techsupport/odkx-prod"
KEEP_DAYS=56                              # keep 8 weekly backups on appliance
MIN_FREE_GB=20                            # abort if less free space than this
STAMP="$(date +%Y%m%d_%H%M)"
DEST="$BACKUP_ROOT/$STAMP"
LOG="$BACKUP_ROOT/backup.log"

# DBTRAC Reporting Configuration
DBTRAC_API_URL="${DBTRAC_API_URL:-http://192.168.35.38:90/api/backup/ingest/}"
DBTRAC_API_KEY="${DBTRAC_API_KEY:-uptimetracker_api_9f2c1d8e45x9}"
START_EPOCH="$(date +%s)"
START_ISO="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

report_to_dbtrac() {
  local outcome="$1"
  local reason="${2:-}"
  local dump_file="${3:-$DEST/ccisprod_db_$STAMP.dump}"
  local end_epoch
  end_epoch="$(date +%s)"
  local end_iso
  end_iso="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
  local dur_mins
  dur_mins="$(awk "BEGIN {printf \"%.2f\", ($end_epoch - $START_EPOCH) / 60.0}")"
  local bkp_name
  bkp_name="$(basename "$dump_file")"
  local is_verified="false"
  if [ "$outcome" = "Success" ]; then
    is_verified="true"
  fi

  curl -fsS -m 15 -X POST "$DBTRAC_API_URL" \
    -H "Content-Type: application/json" \
    -H "X-API-KEY: $DBTRAC_API_KEY" \
    -d "{
      \"database\": \"PostgreSQL\",
      \"database_instance\": \"ccisprod_db\",
      \"backup_type\": \"Full\",
      \"backup_used\": \"${bkp_name}\",
      \"start\": \"${START_ISO}\",
      \"end\": \"${end_iso}\",
      \"duration_mins\": ${dur_mins},
      \"outcome\": \"${outcome}\",
      \"failure_reason\": \"${reason}\",
      \"location\": \"${dump_file}\",
      \"verified\": ${is_verified},
      \"compression\": true
    }" >/dev/null 2>&1 || echo "WARN: Could not notify DBTRAC API at $DBTRAC_API_URL"
}

fail() {
  echo "ERROR: $*"
  echo "===== backup FAILED"
  report_to_dbtrac "Failed" "$*"
  exit 1
}

# 0. Verify Backup Appliance is mounted (Never write to local root '/')
CHECK_PATH="$APPLIANCE_MOUNT"
while [ ! -d "$CHECK_PATH" ] && [ "$CHECK_PATH" != "/" ]; do
  CHECK_PATH="$(dirname "$CHECK_PATH")"
done
TARGET_MNT="$(df -P "$CHECK_PATH" 2>/dev/null | awk 'NR==2 {print $6}')"
if [ -z "$TARGET_MNT" ] || [ "$TARGET_MNT" = "/" ]; then
  report_to_dbtrac "Failed" "Backup appliance path $APPLIANCE_MOUNT is not mounted (resolves to /)"
  echo "ERROR: Backup appliance '$APPLIANCE_MOUNT' resolves to local root '/'! Refusing to write to local disk." >&2
  exit 1
fi

mkdir -p "$DEST"
exec >>"$LOG" 2>&1
echo "===== $(date -u '+%F %T UTC') backup start -> $DEST (Backup Appliance Mount: $TARGET_MNT)"

# 1. Free-space check on Backup Appliance
FREE_GB="$(df -BG --output=avail "$DEST" | tail -1 | tr -dc '0-9')"
[ -n "$FREE_GB" ] && [ "$FREE_GB" -ge "$MIN_FREE_GB" ] || fail "only ${FREE_GB:-0}G free on appliance $BACKUP_ROOT"

# 2. Database (exact name match: avoid ccisprod_db-bootstrap)
DB_C="$(docker ps --format '{{.ID}} {{.Names}}' | awk '$2 ~ /^ccisprod_db\.1\./ {print $1; exit}')"
[ -n "$DB_C" ] || fail "ccisprod_db container not running"

docker exec "$DB_C" sh -c 'pg_dump -U "${POSTGRES_USER:-postgres}" -d postgres -Fc' \
  > "$DEST/ccisprod_db_$STAMP.dump" || fail "pg_dump failed"
docker exec "$DB_C" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}" --globals-only' \
  > "$DEST/ccisprod_globals_$STAMP.sql" || fail "pg_dumpall globals failed"

# Verify the dump is readable via pg_restore -l
docker exec -i "$DB_C" pg_restore -l < "$DEST/ccisprod_db_$STAMP.dump" > /dev/null \
  || fail "dump verification failed"
DUMP_SIZE="$(du -h "$DEST/ccisprod_db_$STAMP.dump" | cut -f1)"
echo "DB dump OK: $DUMP_SIZE"

# 3. LDAP volumes (small; backed up together so schema and data match)
for VOL in ccisprod_ldap-vol ccisprod_ldap-slapd.d-vol; do
  MP="$(docker volume inspect -f '{{.Mountpoint}}' "$VOL" 2>/dev/null)" || { echo "WARN: volume $VOL not found"; continue; }
  tar czf "$DEST/${VOL}_$STAMP.tar.gz" -C "$MP" . || fail "tar of $VOL failed"
done
echo "LDAP volumes OK"

# 4. Stack config (compose, env, nginx configs, scripts) - no source trees / node_modules
tar czf "$DEST/ccisprod_config_$STAMP.tar.gz" -C "$STACK_DIR" \
  --exclude='node_modules' --exclude='*.jar' --exclude='target' \
  docker-compose.yml deploy.sh scripts frontend/.env $(ls -d "$STACK_DIR"/*.conf 2>/dev/null | xargs -r -n1 basename) \
  2>/dev/null || echo "WARN: some config files missing (check list)"

# 5. Checksums
( cd "$DEST" && sha256sum * > SHA256SUMS )

# 6. Retention on Backup Appliance
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -print -exec rm -rf {} +

echo "Total size: $(du -sh "$DEST" | cut -f1)"
echo "===== $(date -u '+%F %T UTC') backup OK"

# 7. Notify DBTRAC Backup & Recovery Register
report_to_dbtrac "Success" "" "$DEST/ccisprod_db_$STAMP.dump ($DUMP_SIZE)"

# ---------------------------------------------------------------------------
# /etc/cron.d/ccisprod-backup   (server clock is UTC; 23:00 Sat UTC = 02:00 Sun Uganda)
# 0 23 * * 6 root /home/techsupport/odkx-prod/scripts/ccisprod-weekly-backup.sh
# ---------------------------------------------------------------------------
