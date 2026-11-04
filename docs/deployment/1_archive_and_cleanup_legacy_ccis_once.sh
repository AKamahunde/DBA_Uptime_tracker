#!/usr/bin/env bash
# ==============================================================================
# 1_archive_and_cleanup_legacy_ccis_once.sh
# Purpose:
#   1. Backs up the legacy 'ccis' Docker Swarm stack (services, databases,
#      volumes, configs, compose files) AND all 'ccis_data-sync' (/logsAndData)
#      containers (~95.5 GB+) ONCE directly to the external Backup Appliance
#      (zero temporary files written to the local root '/' filesystem).
#   2. Verifies non-empty archives, gzip integrity, and SHA256SUMS on the
#      Backup Appliance.
#   3. Safely removes the legacy 'ccis' stack, exited 'ccis_data-sync'
#      containers (~95.5 GB), and legacy 'ccis' volumes from the server so it
#      stops generating hourly snapshots and reclaims disk space.
#
# Safety Guarantees:
#   - Strictly targets ONLY the legacy 'ccis' stack; NEVER touches 'ccisprod'
#     or 'ccisprod_db-vol-pg17'.
#   - Verifies the target path resides on a non-root mounted filesystem (works
#     for both mount roots like /nfsdata and subdirectories like /nfsdata/backup).
#   - Aborts immediately (without deleting any local data) if any archive step
#     or checksum verification fails.
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION (Adjust APPLIANCE_MOUNT to your Backup Appliance path) ---
APPLIANCE_MOUNT="${APPLIANCE_MOUNT:-/nfsdata/backup}"
BACKUP_ROOT="${APPLIANCE_MOUNT}/ccis_legacy_final_archive"
STAMP="$(date +%Y%m%d_%H%M)"
DEST="${BACKUP_ROOT}/${STAMP}"
LOG="${BACKUP_ROOT}/legacy_archive_${STAMP}.log"
MIN_FREE_GB=120

# Cleanup controls:
#   PERFORM_CLEANUP="true"     -> removes exited ccis_data-sync containers & legacy data after verified archive
#   REMOVE_LEGACY_STACK="true" -> runs 'docker stack rm ccis' + removes legacy 'ccis' volumes so it never grows again
PERFORM_CLEANUP="${PERFORM_CLEANUP:-true}"
REMOVE_LEGACY_STACK="${REMOVE_LEGACY_STACK:-true}"

if [[ "${1:-}" == "--archive-only" ]]; then
  PERFORM_CLEANUP="false"
  REMOVE_LEGACY_STACK="false"
fi

fail() {
  echo "[ERROR] $*" | tee -a "${LOG:-/dev/stderr}" >&2
  echo "===== LEGACY ARCHIVE FAILED (No local data was deleted) =====" | tee -a "${LOG:-/dev/stderr}" >&2
  exit 1
}

# 1. Verify APPLIANCE_MOUNT is on an external mounted filesystem (NOT local root '/')
#    First check that the parent mount exists before mkdir -p creates anything.
CHECK_PATH="$APPLIANCE_MOUNT"
while [ ! -d "$CHECK_PATH" ] && [ "$CHECK_PATH" != "/" ]; do
  CHECK_PATH="$(dirname "$CHECK_PATH")"
done

TARGET_MNT="$(df -P "$CHECK_PATH" 2>/dev/null | awk 'NR==2 {print $6}')"
if [ -z "$TARGET_MNT" ] || [ "$TARGET_MNT" = "/" ]; then
  echo "ERROR: '$APPLIANCE_MOUNT' resolves to the local root filesystem ('/') instead of a mounted Backup Appliance!" >&2
  echo "Mount your Backup Appliance first, or run with: APPLIANCE_MOUNT=/path/to/appliance $0" >&2
  exit 1
fi

mkdir -p "$DEST/services" "$DEST/volumes" "$DEST/configs" "$DEST/compose" "$DEST/data_sync_containers"
exec > >(tee -a "$LOG") 2>&1

echo "===== $(date -u '+%F %T UTC') Starting ONE-TIME Legacy 'ccis' Archive -> $DEST (Mount: $TARGET_MNT) ====="

# 2. Check free space on the Backup Appliance
FREE_GB="$(df -BG --output=avail "$DEST" | tail -1 | tr -dc '0-9')"
if [ -z "$FREE_GB" ] || [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
  fail "Only ${FREE_GB:-0}G free on Backup Appliance ($DEST); need at least ${MIN_FREE_GB}G."
fi

STACK="ccis"
LEGACY_VOLUMES_ARCHIVED=()

# 3. Export legacy 'ccis' service definitions, databases, volumes, configs, and compose files
ACTIVE_STACKS="$(docker stack ls --format '{{.Name}}')"
if echo "$ACTIVE_STACKS" | grep -qx "$STACK"; then
  echo "--- Archiving service definitions for legacy stack: $STACK"
  for S in $(docker stack services "$STACK" --format '{{.Name}}'); do
    docker service inspect "$S" > "$DEST/services/${S}.json" || fail "Failed to inspect service $S"
  done

  # 3a. Databases in legacy 'ccis' stack (if any running)
  DB_VOLS=""
  while IFS='|' read -r CID CNAME IMG; do
    if [ -z "$CID" ]; then
      continue
    fi
    if echo "$IMG" | grep -qiE 'postgres|postgis'; then
      SVC="${CNAME%%.*}"
      echo "--- Dumping Postgres database container $CNAME ($CID)"
      docker exec "$CID" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' | gzip -1 > "$DEST/${SVC}_pg_dumpall.sql.gz" \
        || fail "pg_dumpall failed for $SVC"
      gzip -t "$DEST/${SVC}_pg_dumpall.sql.gz" || fail "Corrupt gzip dump for $SVC"
      DB_VOLS="$DB_VOLS $(docker inspect -f '{{range .Mounts}}{{.Name}} {{end}}' "$CID")"
    fi
    if echo "$IMG" | grep -qiE 'mysql|mariadb'; then
      SVC="${CNAME%%.*}"
      echo "--- Dumping MySQL/MariaDB container $CNAME ($CID)"
      docker exec "$CID" sh -c 'mysqldump --all-databases --single-transaction -u root -p"${MYSQL_ROOT_PASSWORD:-}"' | gzip -1 > "$DEST/${SVC}_mysqldump.sql.gz" \
        || fail "mysqldump failed for $SVC"
      gzip -t "$DEST/${SVC}_mysqldump.sql.gz" || fail "Corrupt gzip dump for $SVC"
    fi
  done < <(docker ps --filter "label=com.docker.stack.namespace=$STACK" --format '{{.ID}}|{{.Names}}|{{.Image}}')

  # 3b. Archive ALL named volumes belonging to legacy 'ccis' (including DB volumes for raw block-level safety before removal)
  for V in $(docker volume ls -q --filter "label=com.docker.stack.namespace=$STACK"); do
    if [ -z "$V" ]; then
      continue
    fi
    # Extra safety guard: never touch anything starting with ccisprod
    case "$V" in
      ccisprod*) continue ;;
    esac
    MP="$(docker volume inspect -f '{{.Mountpoint}}' "$V")"
    echo "--- Archiving volume $V ($MP)"
    tar czf "$DEST/volumes/${V}.tar.gz" -C "$MP" . || fail "Failed to tar volume $V"
    gzip -t "$DEST/volumes/${V}.tar.gz" || fail "Archive integrity check failed for volume $V"
    LEGACY_VOLUMES_ARCHIVED+=("$V")
  done

  # 3c. Swarm configs belonging to 'ccis'
  for C in $(docker config ls --filter "label=com.docker.stack.namespace=$STACK" --format '{{.Name}}'); do
    if [ -z "$C" ]; then
      continue
    fi
    docker config inspect -f '{{json .Spec.Data}}' "$C" | tr -d '"' | base64 -d > "$DEST/configs/$C" \
      || fail "Failed to export Swarm config $C"
  done

  # 3d. Compose files referencing 'ccis' (strictly excluding 'ccisprod' and 'odkx-prod')
  COMPOSE_FILES="$(find /home /opt /srv /root -maxdepth 5 \( -name 'docker-compose*.yml' -o -name 'docker-compose*.yaml' -o -name 'stack*.yml' \) -not -path '*/node_modules/*' 2>/dev/null || true)"
  for F in $COMPOSE_FILES; do
    case "$F" in
      *ccisprod*|*odkx-prod*) continue ;;
      */ccis/*|*/ccis-*|*/ccis_*|*docker-compose*ccis*)
        cp --parents "$F" "$DEST/compose/" 2>/dev/null || true
        ;;
    esac
  done
else
  echo "WARN: Stack 'ccis' not currently active in 'docker stack ls'."
fi

# 4. Archive /logsAndData from ALL ccis_data-sync containers (both EXITED and RUNNING)
#    Streams directly over stdout into gzipped tarballs on the Backup Appliance (zero local disk usage).
echo "--- Searching for all ccis_data-sync containers (running and exited)..."
ARCHIVED_DATA_SYNC_CIDS=()
RUNNING_DATA_SYNC_CIDS=()

while IFS='|' read -r CID CNAME CSTATE; do
  if [ -z "$CID" ]; then
    continue
  fi
  echo "--- Archiving /logsAndData from container: $CNAME ($CID, state=$CSTATE)"
  ARCHIVE_FILE="$DEST/data_sync_containers/${CNAME}_${CID}_${CSTATE}_logsAndData.tar.gz"

  # Stream directly from the container's overlay2 layer to the Backup Appliance
  if docker cp "${CID}:/logsAndData" - | gzip -1 > "$ARCHIVE_FILE"; then
    if [ ! -s "$ARCHIVE_FILE" ] || ! gzip -t "$ARCHIVE_FILE"; then
      fail "Archive $ARCHIVE_FILE is empty or failed gzip integrity check!"
    fi
    echo "    Archived OK: $(du -h "$ARCHIVE_FILE" | cut -f1) -> $ARCHIVE_FILE"
    if [ "$CSTATE" = "exited" ] || [ "$CSTATE" = "dead" ]; then
      ARCHIVED_DATA_SYNC_CIDS+=("$CID")
    elif [ "$CSTATE" = "running" ]; then
      RUNNING_DATA_SYNC_CIDS+=("$CID")
    fi
  else
    rm -f "$ARCHIVE_FILE"
    fail "Failed to stream /logsAndData from container $CNAME ($CID). Aborting before any cleanup!"
  fi
done < <(docker ps -a --filter "name=ccis_data-sync" --format '{{.ID}}|{{.Names}}|{{.State}}')

# 5. Generate & Verify SHA256 Checksums on Backup Appliance
echo "--- Generating and verifying SHA256SUMS on Backup Appliance..."
FILE_COUNT="$(find "$DEST" -type f ! -name SHA256SUMS | wc -l)"
if [ "$FILE_COUNT" -eq 0 ]; then
  fail "No files were written to $DEST! Aborting before any cleanup."
fi

( cd "$DEST" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS )
( cd "$DEST" && sha256sum -c SHA256SUMS >/dev/null ) || fail "SHA256 verification failed on Backup Appliance!"
echo "Archive verified OK ($FILE_COUNT files). Total archived size on appliance: $(du -sh "$DEST" | cut -f1)"

# 6. Cleanup old 'ccis' data from local server to reclaim ~95.5 GB+ and stop hourly growth
if [ "$PERFORM_CLEANUP" = "true" ]; then
  echo "--- Starting safe cleanup of archived legacy 'ccis' data on local server..."
  echo "    Local root disk usage BEFORE cleanup: $(df -h / | tail -1)"

  # 6a. If REMOVE_LEGACY_STACK="true", remove the legacy 'ccis' stack so ccis_data-sync never respawns or runs cron again
  if [ "$REMOVE_LEGACY_STACK" = "true" ] && docker stack ls --format '{{.Name}}' | grep -qx "ccis"; then
    echo "    Removing legacy Docker Swarm stack 'ccis' (does NOT touch 'ccisprod')..."
    docker stack rm ccis
    echo "    Waiting 15 seconds for legacy 'ccis' containers to terminate..."
    sleep 15
  else
    # Even if keeping the stack, stop the hourly cron job and clean /logsAndData inside any running ccis_data-sync container
    if [ "${#RUNNING_DATA_SYNC_CIDS[@]}" -gt 0 ]; then
      for RCID in "${RUNNING_DATA_SYNC_CIDS[@]}"; do
        echo "    Stopping cron & cleaning archived snapshots inside running container $RCID:/logsAndData..."
        docker exec "$RCID" sh -c 'rm -f /etc/cron*/*sync* /var/spool/cron/crontabs/* 2>/dev/null || true; pkill -f cron 2>/dev/null || true; rm -rf /logsAndData/* 2>/dev/null || true'
      done
    fi
  fi

  # 6b. Remove all exited ccis_data-sync containers (~79.5 GB to ~95.5 GB)
  for ECID in $(docker ps -aq --filter "name=ccis_data-sync" --filter "status=exited" --filter "status=dead"); do
    if [ -z "$ECID" ]; then
      continue
    fi
    echo "    Removing exited ccis_data-sync container $ECID..."
    docker rm "$ECID"
  done

  # 6c. If legacy stack was removed, also remove any remaining exited 'ccis' stack containers and archived 'ccis' volumes
  if [ "$REMOVE_LEGACY_STACK" = "true" ]; then
    for SCID in $(docker ps -aq --filter "label=com.docker.stack.namespace=ccis" --filter "status=exited"); do
      if [ -z "$SCID" ]; then
        continue
      fi
      echo "    Removing exited legacy 'ccis' container $SCID..."
      docker rm "$SCID" || true
    done

    if [ "${#LEGACY_VOLUMES_ARCHIVED[@]}" -gt 0 ]; then
      for V in "${LEGACY_VOLUMES_ARCHIVED[@]}"; do
        case "$V" in
          ccisprod*) continue ;;
        esac
        echo "    Removing archived legacy volume $V..."
        docker volume rm "$V" || echo "    WARN: Volume $V still in use; skipped."
      done
    fi
  fi

  echo "    Local root disk usage AFTER cleanup:  $(df -h / | tail -1)"
else
  echo "--- Archive-only mode requested; skipped local container/stack deletion."
fi

echo "===== $(date -u '+%F %T UTC') ONE-TIME LEGACY CCIS ARCHIVE & CLEANUP COMPLETE ====="
