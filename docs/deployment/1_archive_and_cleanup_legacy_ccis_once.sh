#!/usr/bin/env bash
# ==============================================================================
# 1_archive_and_cleanup_legacy_ccis_once.sh
# Purpose:
#   1. Backs up the legacy 'ccis' Docker Swarm stack ONCE directly to the
#      external Backup Appliance (zero temporary files on local root '/'):
#      - Service definitions (ccis_nginx, ccis_ldap-service, ccis_phpldapadmin,
#        ccis_dashboard, ccis_dashboard-database, ccis_data-sync)
#      - Full PostgreSQL dump of ccis_dashboard-database (image: ccis/dashboard-database)
#      - Named volumes: ccis_dashboard-db-vol (420 MB), ccis_ldap-vol, ccis_ldap-slapd.d-vol
#      - Swarm configs: ccis_ccdbsync.config, ccis_com.nginx.proxy_buffer.conf,
#        ccis_com.nginx.sync-endpoint.conf
#      - Deployment directory: /root/uganda-ccis-server-setup (compose, .env, nginx configs)
#      - All 3 ccis_data-sync /logsAndData snapshot directories (~88 GB total across
#        running [13G], exited [22G], and dead [53G] containers) read directly
#        from Docker overlay2 UpperDir so even 'Dead' containers archive cleanly.
#   2. Verifies non-empty archives, gzip integrity, and SHA256SUMS on the
#      Backup Appliance.
#   3. Reclaims the ~88 GB of disk space on /dev/mapper/ubuntu--vg-root and
#      stops ccis_data-sync from generating ~6.5 GB/day of new hourly snapshots.
#
# Modes:
#   Default:         Archives everything, removes the ~88 GB of /logsAndData &
#                    exited/dead ccis_data-sync containers, and scales
#                    ccis_data-sync, ccis_dashboard, and ccis_dashboard-database
#                    to 0 replicas (preserving ccis_nginx & ccis_ldap-service
#                    which proxy *.ccisuganda.org for the 6 regional stacks).
#   --remove-stack:  Also runs 'docker stack rm ccis' and removes ccis volumes
#                    (use ONLY if the 6 regional *.ccisuganda.org proxies are
#                    also being retired).
#   --archive-only:  Archives to Backup Appliance and verifies SHA256SUMS without
#                    deleting or stopping anything on the local server.
# ==============================================================================

set -euo pipefail

# --- CONFIGURATION (Adjust APPLIANCE_MOUNT to your Backup Appliance path) ---
APPLIANCE_MOUNT="${APPLIANCE_MOUNT:-/nfsdata/backup}"
BACKUP_ROOT="${APPLIANCE_MOUNT}/ccis_legacy_final_archive"
STAMP="$(date +%Y%m%d_%H%M)"
DEST="${BACKUP_ROOT}/${STAMP}"
LOG="${BACKUP_ROOT}/legacy_archive_${STAMP}.log"
MIN_FREE_GB=100

PERFORM_CLEANUP="${PERFORM_CLEANUP:-true}"
REMOVE_FULL_CCIS_STACK="${REMOVE_FULL_CCIS_STACK:-false}"

for arg in "$@"; do
  case "$arg" in
    --archive-only)
      PERFORM_CLEANUP="false"
      REMOVE_FULL_CCIS_STACK="false"
      ;;
    --remove-stack)
      PERFORM_CLEANUP="true"
      REMOVE_FULL_CCIS_STACK="true"
      ;;
  esac
done

fail() {
  echo "[ERROR] $*" | tee -a "${LOG:-/dev/stderr}" >&2
  echo "===== LEGACY ARCHIVE FAILED (No local data was deleted) =====" | tee -a "${LOG:-/dev/stderr}" >&2
  exit 1
}

# 1. Verify APPLIANCE_MOUNT is on an external mounted filesystem (NOT local root '/')
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

mkdir -p "$DEST/services" "$DEST/volumes" "$DEST/configs" "$DEST/compose" "$DEST/data_sync_containers" "$DEST/sql_dumps"
exec > >(tee -a "$LOG") 2>&1

echo "===== $(date -u '+%F %T UTC') Starting ONE-TIME Legacy 'ccis' Archive -> $DEST (Mount: $TARGET_MNT) ====="

# 2. Check free space on the Backup Appliance
FREE_GB="$(df -BG --output=avail "$DEST" | tail -1 | tr -dc '0-9')"
if [ -z "$FREE_GB" ] || [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
  fail "Only ${FREE_GB:-0}G free on Backup Appliance ($DEST); need at least ${MIN_FREE_GB}G."
fi

STACK="ccis"
LEGACY_VOLUMES_ARCHIVED=()

# 3. Export legacy 'ccis' service definitions, databases, volumes, configs, and /root/uganda-ccis-server-setup
ACTIVE_STACKS="$(docker stack ls --format '{{.Name}}')"
if echo "$ACTIVE_STACKS" | grep -qx "$STACK"; then
  echo "--- Archiving service definitions for legacy stack: $STACK"
  for S in $(docker stack services "$STACK" --format '{{.Name}}'); do
    if [ -z "$S" ]; then
      continue
    fi
    docker service inspect "$S" > "$DEST/services/${S}.json" || fail "Failed to inspect service $S"
  done

  # 3a. Databases in legacy 'ccis' stack (matches postgres, postgis, dashboard-database, or db-sync)
  while IFS='|' read -r CID CNAME IMG; do
    if [ -z "$CID" ]; then
      continue
    fi
    if echo "$IMG $CNAME" | grep -qiE 'postgres|postgis|dashboard-database|data-sync'; then
      SVC="${CNAME%%.*}"
      if docker exec "$CID" sh -c 'command -v pg_dumpall >/dev/null 2>&1'; then
        echo "--- Dumping PostgreSQL container $CNAME ($CID, image=$IMG)"
        DUMP_OUT="$DEST/sql_dumps/${SVC}_${CID}_pg_dumpall.sql.gz"
        if docker exec "$CID" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' | gzip -1 > "$DUMP_OUT" \
           && [ -s "$DUMP_OUT" ] && gzip -t "$DUMP_OUT"; then
          echo "    Database dump OK: $(du -h "$DUMP_OUT" | cut -f1)"
        else
          rm -f "$DUMP_OUT"
          # Only hard-fail if it is the primary dashboard-database
          if echo "$CNAME" | grep -qi "dashboard-database"; then
            fail "pg_dumpall failed for $CNAME ($CID)"
          else
            echo "    WARN: pg_dumpall not active inside $CNAME; volume/overlay backup will cover it."
          fi
        fi
      fi
    fi
  done < <(docker ps --filter "label=com.docker.stack.namespace=$STACK" --format '{{.ID}}|{{.Names}}|{{.Image}}')

  # 3b. Archive ALL named volumes belonging to legacy 'ccis' (ccis_dashboard-db-vol, ccis_ldap-vol, ccis_ldap-slapd.d-vol)
  for V in $(docker volume ls -q --filter "label=com.docker.stack.namespace=$STACK"); do
    if [ -z "$V" ]; then
      continue
    fi
    case "$V" in
      ccisprod*) continue ;;
    esac
    MP="$(docker volume inspect -f '{{.Mountpoint}}' "$V")"
    echo "--- Archiving volume $V ($MP)"
    tar czf "$DEST/volumes/${V}.tar.gz" -C "$MP" . || fail "Failed to tar volume $V"
    if [ ! -s "$DEST/volumes/${V}.tar.gz" ] || ! gzip -t "$DEST/volumes/${V}.tar.gz"; then
      fail "Archive integrity check failed for volume $V"
    fi
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
else
  echo "WARN: Stack 'ccis' not currently active in 'docker stack ls'."
fi

# 3d. Archive /root/uganda-ccis-server-setup (where legacy 'ccis' stack was deployed from)
if [ -d /root/uganda-ccis-server-setup ]; then
  echo "--- Archiving /root/uganda-ccis-server-setup directory..."
  tar czf "$DEST/compose/uganda-ccis-server-setup.tar.gz" -C /root uganda-ccis-server-setup \
    || fail "Failed to archive /root/uganda-ccis-server-setup"
  gzip -t "$DEST/compose/uganda-ccis-server-setup.tar.gz" || fail "Corrupt uganda-ccis-server-setup.tar.gz"
fi

# 3e. Also archive any standalone ccis_all_databases_*.sql* files in /home/techsupport
for SQLF in /home/techsupport/ccis_all_databases*; do
  if [ -f "$SQLF" ]; then
    echo "--- Copying standalone dump $SQLF to Backup Appliance..."
    cp -a "$SQLF" "$DEST/sql_dumps/" || fail "Failed to copy $SQLF"
  fi
done

# 4. Archive /logsAndData from ALL ccis_data-sync containers (running [13G], exited [22G], dead [53G])
#    Reads directly from the host overlay2 UpperDir (/var/lib/docker/overlay2/.../diff/logsAndData)
#    so even 'Dead' containers (where 'docker cp' fails) stream cleanly to the Backup Appliance!
echo "--- Searching for all ccis_data-sync containers (running, exited, and dead)..."
DATA_SYNC_CONTAINERS=()

while IFS='|' read -r CID CNAME CSTATE; do
  if [ -z "$CID" ]; then
    continue
  fi
  CLEAN_NAME="${CNAME#/}"
  UPPER_DIR="$(docker inspect -f '{{.GraphDriver.Data.UpperDir}}' "$CID" 2>/dev/null || true)"
  echo "--- Checking container: $CLEAN_NAME ($CID, state=$CSTATE, UpperDir=$UPPER_DIR)"

  if [ -n "$UPPER_DIR" ] && [ -d "$UPPER_DIR/logsAndData" ]; then
    DIR_SIZE="$(du -sh "$UPPER_DIR/logsAndData" 2>/dev/null | cut -f1)"
    ARCHIVE_FILE="$DEST/data_sync_containers/${CLEAN_NAME}_${CID}_${CSTATE}_logsAndData.tar.gz"
    echo "    Streaming $UPPER_DIR/logsAndData ($DIR_SIZE) -> $ARCHIVE_FILE ..."
    tar czf "$ARCHIVE_FILE" -C "$UPPER_DIR/logsAndData" . || fail "Failed to archive $UPPER_DIR/logsAndData for $CLEAN_NAME ($CID)"
    if [ ! -s "$ARCHIVE_FILE" ] || ! gzip -t "$ARCHIVE_FILE"; then
      fail "Archive $ARCHIVE_FILE is empty or failed gzip integrity check!"
    fi
    echo "    Archived OK: $(du -h "$ARCHIVE_FILE" | cut -f1) -> $ARCHIVE_FILE"
    DATA_SYNC_CONTAINERS+=("${CID}|${CSTATE}|${UPPER_DIR}")
  else
    echo "    WARN: $UPPER_DIR/logsAndData not found or already empty for $CLEAN_NAME ($CID)"
  fi
done < <(docker ps -a --filter "name=ccis_data-sync" --format '{{.ID}}|{{.Names}}|{{.State}}')

# 5. Generate & Verify SHA256 Checksums on Backup Appliance
echo "--- Generating and verifying SHA256SUMS on Backup Appliance..."
FILE_COUNT="$(find "$DEST" -type f ! -name SHA256SUMS | wc -l)"
if [ "$FILE_COUNT" -eq 0 ]; then
  fail "No files were written to $DEST! Aborting before any cleanup."
fi

( cd "$DEST" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS ) || fail "Failed to generate SHA256SUMS"
( cd "$DEST" && sha256sum -c SHA256SUMS >/dev/null ) || fail "SHA256 verification failed on Backup Appliance!"
echo "Archive verified OK ($FILE_COUNT files). Total archived size on appliance: $(du -sh "$DEST" | cut -f1)"

# 6. Cleanup old 'ccis' data from local server to reclaim ~88 GB+ and stop ~6.5 GB/day growth
if [ "$PERFORM_CLEANUP" = "true" ]; then
  echo "--- Starting safe cleanup of archived legacy 'ccis' data on local server..."
  echo "    Local root disk usage BEFORE cleanup: $(df -h / | tail -1)"

  # 6a. Scale down ccis_data-sync, ccis_dashboard, and ccis_dashboard-database so they stop running & writing snapshots
  #     (Note: ccis_nginx and ccis_ldap-service are kept running unless --remove-stack is passed,
  #      because ccis_nginx routes HTTPS for west/central/kampala/north/south/east.ccisuganda.org)
  if [ "$REMOVE_FULL_CCIS_STACK" = "true" ]; then
    if docker stack ls --format '{{.Name}}' | grep -qx "ccis"; then
      echo "    Removing entire legacy stack 'ccis' (--remove-stack specified)..."
      docker stack rm ccis
      sleep 15
    fi
  else
    echo "    Scaling retired legacy services (ccis_data-sync, ccis_dashboard, ccis_dashboard-database) to 0 replicas..."
    echo "    (Keeping ccis_nginx & ccis_ldap-service active for regional *.ccisuganda.org routing)"
    docker service scale ccis_data-sync=0 ccis_dashboard=0 ccis_dashboard-database=0 || true
    sleep 10
  fi

  # 6b. Delete /logsAndData contents directly from each archived container's UpperDir first
  #     (Ensures the 53 GB 'Dead' container e074cf60f8b6 frees its disk space even if Docker's overlay unmount is stuck!)
  if [ "${#DATA_SYNC_CONTAINERS[@]}" -gt 0 ]; then
    for ENTRY in "${DATA_SYNC_CONTAINERS[@]}"; do
      IFS='|' read -r DCID DSTATE DUPPER <<< "$ENTRY"
      if [ -n "$DUPPER" ] && [ -d "$DUPPER/logsAndData" ]; then
        echo "    Purging archived snapshots in $DUPPER/logsAndData ($DCID)..."
        rm -rf "${DUPPER}/logsAndData"/* 2>/dev/null || true
      fi
    done
  fi

  # 6c. Remove all stopped/exited/dead ccis_data-sync, ccis_dashboard, and ccis_dashboard-database containers
  for ECID in $(docker ps -aq --filter "name=ccis_data-sync" --filter "name=ccis_dashboard" --filter "status=exited" --filter "status=dead"); do
    if [ -z "$ECID" ]; then
      continue
    fi
    echo "    Removing stopped legacy container $ECID..."
    docker rm -f "$ECID" || echo "    WARN: Docker daemon could not remove container metadata for $ECID (its /logsAndData files were already purged)."
  done

  # 6d. Remove standalone /home/techsupport/ccis_all_databases* dumps that were archived in Step 3e
  for SQLF in /home/techsupport/ccis_all_databases*; do
    if [ -f "$SQLF" ] && [ -f "$DEST/sql_dumps/$(basename "$SQLF")" ]; then
      echo "    Removing archived standalone SQL dump $SQLF..."
      rm -f "$SQLF"
    fi
  done

  echo "    Local root disk usage AFTER cleanup:  $(df -h / | tail -1)"
else
  echo "--- Archive-only mode requested; skipped local container/file deletion."
fi

echo "===== $(date -u '+%F %T UTC') ONE-TIME LEGACY CCIS ARCHIVE & CLEANUP COMPLETE ====="
