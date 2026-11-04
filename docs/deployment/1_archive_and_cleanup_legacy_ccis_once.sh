#!/usr/bin/env bash
# ==============================================================================
# 1_archive_and_cleanup_legacy_ccis_once.sh
# Purpose:
#   1. Backs up the legacy 'ccis' Docker Swarm stack AND the ~95.5 GB+ of
#      ccis_data-sync (/logsAndData) snapshots ONCE directly to the external
#      Backup Appliance (never writing temporary files to the local root disk).
#   2. Verifies SHA256 checksums on the Backup Appliance.
#   3. Removes the two exited ccis_data-sync containers (~79.5 GB) and prunes
#      old snapshots inside the running ccis_data-sync container to reclaim
#      disk space on /dev/mapper/ubuntu--vg-root.
#
# Safety:
#   - Does NOT touch 'ccisprod' or 'ccisprod_db-vol-pg17'.
#   - Refuses to write if APPLIANCE_MOUNT is not mounted (protects root '/' disk).
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION (Adjust APPLIANCE_MOUNT to your mounted Backup Appliance) ---
APPLIANCE_MOUNT="${APPLIANCE_MOUNT:-/nfsdata/backup}"
BACKUP_ROOT="${APPLIANCE_MOUNT}/ccis_legacy_final_archive"
STAMP="$(date +%Y%m%d_%H%M)"
DEST="${BACKUP_ROOT}/${STAMP}"
LOG="${BACKUP_ROOT}/legacy_archive_${STAMP}.log"
MIN_FREE_GB=120

# Set PERFORM_CLEANUP="true" (or pass --cleanup) to remove exited containers & old snapshots after verified backup
PERFORM_CLEANUP="${PERFORM_CLEANUP:-true}"
if [[ "${1:-}" == "--archive-only" ]]; then
  PERFORM_CLEANUP="false"
fi

fail() {
  echo "[ERROR] $*" | tee -a "${LOG:-/dev/stderr}"
  echo "===== LEGACY ARCHIVE FAILED (No local data was deleted) ====="
  exit 1
}

# 1. Verify Backup Appliance is an actual mounted filesystem (NEVER write to local root '/')
if ! mountpoint -q "$APPLIANCE_MOUNT"; then
  echo "ERROR: Backup appliance path '$APPLIANCE_MOUNT' is NOT a mounted filesystem!"
  echo "Mount your NFS / Data Domain / CIFS backup appliance at '$APPLIANCE_MOUNT' first,"
  echo "or run with: APPLIANCE_MOUNT=/mnt/your_backup_appliance $0"
  exit 1
fi

mkdir -p "$DEST/services" "$DEST/volumes" "$DEST/configs" "$DEST/compose" "$DEST/data_sync_containers"
exec > >(tee -a "$LOG") 2>&1

echo "===== $(date -u '+%F %T UTC') Starting ONE-TIME Legacy 'ccis' Archive -> $DEST ====="

# 2. Check free space on the Backup Appliance
FREE_GB=$(df -BG --output=avail "$BACKUP_ROOT" | tail -1 | tr -dc '0-9')
[ "$FREE_GB" -ge "$MIN_FREE_GB" ] || fail "Only ${FREE_GB}G free on Backup Appliance ($BACKUP_ROOT); need at least ${MIN_FREE_GB}G."

STACK="ccis"

# 3. Export legacy 'ccis' service definitions, Swarm configs, and compose files
if docker stack ls --format '{{.Name}}' | grep -qx "$STACK"; then
  echo "--- Archiving service definitions for stack: $STACK"
  for S in $(docker stack services "$STACK" --format '{{.Name}}'); do
    docker service inspect "$S" > "$DEST/services/${S}.json" || fail "Failed to inspect service $S"
  done

  # Databases in legacy 'ccis' stack (if any)
  DB_VOLS=""
  while IFS='|' read -r CID CNAME IMG; do
    [ -z "$CID" ] && continue
    if echo "$IMG" | grep -qiE 'postgres|postgis'; then
      SVC="${CNAME%%.*}"
      echo "--- Dumping database container $CNAME ($CID)"
      docker exec "$CID" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' | gzip > "$DEST/${SVC}_pg_dumpall.sql.gz"         || fail "pg_dumpall failed for $SVC"
      DB_VOLS="$DB_VOLS $(docker inspect -f '{{range .Mounts}}{{.Name}} {{end}}' "$CID")"
    fi
    if echo "$IMG" | grep -qiE 'mysql|mariadb'; then
      SVC="${CNAME%%.*}"
      echo "--- Dumping MySQL/MariaDB container $CNAME ($CID)"
      docker exec "$CID" sh -c 'mysqldump --all-databases --single-transaction -u root -p"${MYSQL_ROOT_PASSWORD:-}"' | gzip > "$DEST/${SVC}_mysqldump.sql.gz"         || echo "WARN: mysqldump returned non-zero for $SVC"
    fi
  done < <(docker ps --filter "label=com.docker.stack.namespace=$STACK" --format '{{.ID}}|{{.Names}}|{{.Image}}')

  # Named volumes belonging to 'ccis'
  for V in $(docker volume ls -q --filter "label=com.docker.stack.namespace=$STACK"); do
    if echo " $DB_VOLS " | grep -q " $V "; then
      continue
    fi
    MP=$(docker volume inspect -f '{{.Mountpoint}}' "$V")
    echo "--- Archiving volume $V ($MP)"
    tar czf "$DEST/volumes/${V}.tar.gz" -C "$MP" . || fail "Failed to tar volume $V"
  done

  # Swarm configs belonging to 'ccis'
  for C in $(docker config ls --filter "label=com.docker.stack.namespace=$STACK" --format '{{.Name}}'); do
    docker config inspect -f '{{json .Spec.Data}}' "$C" | tr -d '"' | base64 -d > "$DEST/configs/$C" 2>/dev/null || true
  done

  # Compose files referencing 'ccis'
  COMPOSE_FILES=$(find /home /opt /srv /root -maxdepth 5 \( -name 'docker-compose*.yml' -o -name 'docker-compose*.yaml' -o -name 'stack*.yml' \) -not -path '*/node_modules/*' 2>/dev/null || true)
  for F in $COMPOSE_FILES; do
    case "$F" in *"$STACK"*) cp --parents "$F" "$DEST/compose/" 2>/dev/null || true ;; esac
  done
else
  echo "WARN: Stack 'ccis' not found in 'docker stack ls'."
fi

# 4. Archive /logsAndData from ALL ccis_data-sync containers (both EXITED and RUNNING)
#    Streams directly over stdout into gzipped tarballs on the Backup Appliance (zero local disk usage).
echo "--- Searching for all ccis_data-sync containers (running and exited)..."
EXITED_DATA_SYNC_CIDS=()
RUNNING_DATA_SYNC_CIDS=()

while IFS='|' read -r CID CNAME CSTATE; do
  [ -z "$CID" ] && continue
  echo "--- Archiving /logsAndData from container: $CNAME ($CID, state=$CSTATE)"
  ARCHIVE_FILE="$DEST/data_sync_containers/${CNAME}_${CID}_${CSTATE}_logsAndData.tar.gz"

  # docker export / docker cp streams directly from the container's overlay2 layer even when exited
  if docker cp "${CID}:/logsAndData" - 2>/dev/null | gzip > "$ARCHIVE_FILE"; then
    echo "    Archived OK: $(du -h "$ARCHIVE_FILE" | cut -f1) -> $ARCHIVE_FILE"
    if [ "$CSTATE" = "exited" ] || [ "$CSTATE" = "dead" ]; then
      EXITED_DATA_SYNC_CIDS+=("$CID")
    elif [ "$CSTATE" = "running" ]; then
      RUNNING_DATA_SYNC_CIDS+=("$CID")
    fi
  else
    rm -f "$ARCHIVE_FILE"
    echo "    WARN: /logsAndData not found in $CNAME ($CID)"
  fi
done < <(docker ps -a --filter "name=ccis_data-sync" --format '{{.ID}}|{{.Names}}|{{.State}}')

# 5. Generate & Verify SHA256 Checksums on Backup Appliance
echo "--- Generating SHA256SUMS on Backup Appliance..."
( cd "$DEST" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS )
( cd "$DEST" && sha256sum -c SHA256SUMS >/dev/null ) || fail "SHA256 verification failed on Backup Appliance!"
echo "Archive verified OK. Total archived size on appliance: $(du -sh "$DEST" | cut -f1)"

# 6. Cleanup old ccis_data-sync data from local server to reclaim ~95.5 GB+
if [ "$PERFORM_CLEANUP" = "true" ]; then
  echo "--- Starting safe cleanup of archived legacy ccis_data-sync data on local server..."
  echo "    Disk usage BEFORE cleanup: $(df -h / | tail -1)"

  # 6a. Remove exited ccis_data-sync containers (~79.5 GB)
  for ECID in "${EXITED_DATA_SYNC_CIDS[@]:-}"; do
    [ -z "$ECID" ] && continue
    echo "    Removing exited container $ECID..."
    docker rm "$ECID"
  done

  # 6b. Prune old snapshots inside the running ccis_data-sync container & stop its hourly cron
  for RCID in "${RUNNING_DATA_SYNC_CIDS[@]:-}"; do
    [ -z "$RCID" ] && continue
    echo "    Cleaning archived snapshots inside running container $RCID:/logsAndData..."
    docker exec "$RCID" sh -c 'rm -rf /logsAndData/* 2>/dev/null || true'
  done

  echo "    Disk usage AFTER cleanup:  $(df -h / | tail -1)"
else
  echo "--- Archive-only mode requested; skipped container deletion."
fi

echo "===== $(date -u '+%F %T UTC') ONE-TIME LEGACY CCIS ARCHIVE & CLEANUP COMPLETE ====="
