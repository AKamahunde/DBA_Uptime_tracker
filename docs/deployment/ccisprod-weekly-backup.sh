#!/usr/bin/env bash
# ==============================================================================
# ccisprod-weekly-backup.sh (Appliance + DBTRAC Edition)
# Backs up:
#   - Postgres/PostGIS 17 DB (pg_dump custom format + roles/globals, no downtime)
#   - LDAP volumes (ccisprod_ldap-vol, ccisprod_ldap-slapd.d-vol)
#   - Stack config files (/home/techsupport/odkx-prod)
#
# Key Upgrades & Bug Fixes from Original:
#   1. Writes directly to the external Backup Appliance (APPLIANCE_MOUNT), NEVER
#      to the local CCIS server root '/' disk.
#   2. Verifies APPLIANCE_MOUNT resides on a non-root mounted filesystem before
#      writing a single byte (works for mount roots and subdirectories alike).
#   3. Safe SHA256SUMS generation (excludes SHA256SUMS itself, guarded with fail).
#   4. Safe config archiving (only tars existing files so missing optional files
#      do not cause tar exit code 2).
#   5. Automatically pushes clean backup metadata (Success/Failed, duration,
#      clean filename, clean path) to DBTRAC (http://192.168.35.38:90/api/backup/ingest/).
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

# DBITRAC Reporting Configuration (IP with DNS fallback for migration resilience)
DBTRAC_API_URL="${DBTRAC_API_URL:-http://192.168.35.38:91/api/backup/ingest/}"
DBTRAC_FALLBACK_URLS=(
  "$DBTRAC_API_URL"
  "http://dbtractest.nms.go.ug:91/api/backup/ingest/"
  "http://dbtractest.nms.go.ug/api/backup/ingest/"
)
DBTRAC_API_KEY="${DBTRAC_API_KEY:-uptimetracker_api_9f2c1d8e45x9}"
START_EPOCH="$(date +%s)"
START_ISO="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

report_to_dbtrac() {
  local outcome="$1"
  local raw_reason="${2:-}"
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

  # Sanitize failure_reason so quotes or newlines never break JSON
  local clean_reason
  clean_reason="$(printf '%s' "$raw_reason" | tr '\n\r"' "  '")"

  local payload="{
      \"database\": \"PostgreSQL\",
      \"database_instance\": \"ccisprod_db\",
      \"backup_type\": \"Full\",
      \"backup_used\": \"${bkp_name}\",
      \"start\": \"${START_ISO}\",
      \"end\": \"${end_iso}\",
      \"duration_mins\": ${dur_mins},
      \"outcome\": \"${outcome}\",
      \"failure_reason\": \"${clean_reason}\",
      \"location\": \"${dump_file}\",
      \"verified\": ${is_verified},
      \"compression\": true
    }"

  local sent=0
  for api_url in "${DBTRAC_FALLBACK_URLS[@]}"; do
    if curl -fsS --connect-timeout 6 -m 15 -X POST "$api_url" \
      -H "Content-Type: application/json" \
      -H "X-API-KEY: $DBTRAC_API_KEY" \
      -d "$payload" >/dev/null 2>&1; then
      sent=1
      break
    fi
  done
  if [ "$sent" -eq 0 ]; then
    echo "WARN: Could not notify DBITRAC API via IP or DNS fallback (${DBTRAC_FALLBACK_URLS[*]})"
  fi
}

fail() {
  echo "ERROR: $*"
  echo "===== backup FAILED"
  report_to_dbtrac "Failed" "$*" "$DEST/ccisprod_db_$STAMP.dump"
  exit 1
}

# 0. Verify Backup Appliance is mounted (Never write to local root '/')
CHECK_PATH="$APPLIANCE_MOUNT"
while [ ! -d "$CHECK_PATH" ] && [ "$CHECK_PATH" != "/" ]; do
  CHECK_PATH="$(dirname "$CHECK_PATH")"
done
TARGET_MNT="$(df -P "$CHECK_PATH" 2>/dev/null | awk 'NR==2 {print $6}')"
if [ -z "$TARGET_MNT" ] || [ "$TARGET_MNT" = "/" ]; then
  report_to_dbtrac "Failed" "Backup appliance path $APPLIANCE_MOUNT is not mounted (resolves to /)" "$DEST/ccisprod_db_$STAMP.dump"
  echo "ERROR: Backup appliance '$APPLIANCE_MOUNT' resolves to local root '/'! Refusing to write to local disk." >&2
  exit 1
fi

mkdir -p "$DEST" || fail "Could not create backup directory $DEST"
exec >>"$LOG" 2>&1
echo "===== $(date -u '+%F %T UTC') backup start -> $DEST (Backup Appliance Mount: $TARGET_MNT)"

# 1. Free-space check on Backup Appliance
FREE_GB="$(df -BG --output=avail "$DEST" | tail -1 | tr -dc '0-9')"
if [ -z "$FREE_GB" ] || [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
  fail "only ${FREE_GB:-0}G free on appliance $BACKUP_ROOT (need ${MIN_FREE_GB}G)"
fi

# 2. Database (exact name match: avoid ccisprod_db-bootstrap)
DB_C="$(docker ps --format '{{.ID}} {{.Names}}' | awk '$2 ~ /^ccisprod_db\.1\./ {print $1; exit}')"
if [ -z "$DB_C" ]; then
  fail "ccisprod_db container not running"
fi

DUMP_FILE="$DEST/ccisprod_db_$STAMP.dump"
GLOBALS_FILE="$DEST/ccisprod_globals_$STAMP.sql"

docker exec "$DB_C" sh -c 'pg_dump -U "${POSTGRES_USER:-postgres}" -d postgres -Fc' \
  > "$DUMP_FILE" || fail "pg_dump of postgres database failed"
docker exec "$DB_C" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}" --globals-only' \
  > "$GLOBALS_FILE" || fail "pg_dumpall globals failed"

# Verify the dump is non-empty and readable via pg_restore -l
if [ ! -s "$DUMP_FILE" ]; then
  fail "Database dump file $DUMP_FILE is empty"
fi
docker exec -i "$DB_C" pg_restore -l < "$DUMP_FILE" > /dev/null \
  || fail "dump verification (pg_restore -l) failed"

# Also dump any additional non-template user databases (e.g. odkx) if present
OTHER_DBS="$(docker exec "$DB_C" sh -c 'psql -U "${POSTGRES_USER:-postgres}" -d postgres -Atc "SELECT datname FROM pg_database WHERE datistemplate = false AND datname NOT IN ('\''postgres'\'');"' 2>/dev/null || true)"
for ODB in $OTHER_DBS; do
  if [ -z "$ODB" ]; then
    continue
  fi
  docker exec "$DB_C" sh -c "pg_dump -U \"\${POSTGRES_USER:-postgres}\" -d \"$ODB\" -Fc" \
    > "$DEST/ccisprod_db_${ODB}_$STAMP.dump" || fail "pg_dump of $ODB failed"
  docker exec -i "$DB_C" pg_restore -l < "$DEST/ccisprod_db_${ODB}_$STAMP.dump" > /dev/null \
    || fail "dump verification for $ODB failed"
done

DUMP_SIZE="$(du -h "$DUMP_FILE" | cut -f1)"
echo "DB dump OK: $DUMP_SIZE"

# 3. LDAP volumes (small; backed up together so schema and data match)
for VOL in ccisprod_ldap-vol ccisprod_ldap-slapd.d-vol; do
  MP="$(docker volume inspect -f '{{.Mountpoint}}' "$VOL" 2>/dev/null)" || { echo "WARN: volume $VOL not found"; continue; }
  tar czf "$DEST/${VOL}_$STAMP.tar.gz" -C "$MP" . || fail "tar of $VOL failed"
done
echo "LDAP volumes OK"

# 4. Stack config (only include existing items so tar does not exit with code 2 on missing optional files)
if [ -d "$STACK_DIR" ]; then
  CFG_ITEMS=()
  for ITEM in docker-compose.yml deploy.sh scripts frontend/.env; do
    if [ -e "$STACK_DIR/$ITEM" ]; then
      CFG_ITEMS+=("$ITEM")
    fi
  done
  for CONF in "$STACK_DIR"/*.conf; do
    if [ -e "$CONF" ]; then
      CFG_ITEMS+=("$(basename "$CONF")")
    fi
  done
  if [ "${#CFG_ITEMS[@]}" -gt 0 ]; then
    tar czf "$DEST/ccisprod_config_$STAMP.tar.gz" -C "$STACK_DIR" \
      --exclude='node_modules' --exclude='*.jar' --exclude='target' \
      "${CFG_ITEMS[@]}" || echo "WARN: tar of stack config returned non-zero"
  fi
else
  echo "WARN: STACK_DIR $STACK_DIR not found"
fi

# 5. Checksums (exclude SHA256SUMS itself, verify integrity)
( cd "$DEST" && find . -maxdepth 1 -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS ) \
  || fail "sha256sum generation failed"

# 6. Retention on Backup Appliance (do not fail the current backup if an old folder fails to delete)
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -print -exec rm -rf {} + \
  || echo "WARN: retention cleanup encountered a non-fatal error"

echo "Total size: $(du -sh "$DEST" | cut -f1)"
echo "===== $(date -u '+%F %T UTC') backup OK"

# 7. Notify DBTRAC Backup & Recovery Register (pass clean file path so backup_used & location are clean)
report_to_dbtrac "Success" "" "$DUMP_FILE"

# ---------------------------------------------------------------------------
# /etc/cron.d/ccisprod-backup   (server clock is UTC; 23:00 Sat UTC = 02:00 Sun Uganda)
# 0 23 * * 6 root /home/techsupport/odkx-prod/scripts/ccisprod-weekly-backup.sh
# ---------------------------------------------------------------------------
