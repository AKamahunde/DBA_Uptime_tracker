#!/usr/bin/env bash
# ==============================================================================
# all-stacks-weekly-backup.sh (Appliance Edition — Excludes Legacy 'ccis' Stack)
# Weekly backup of active Docker Swarm stacks on the CCIS Uganda server
# (ccisprod, central, east, kampala, north, south, west — excludes legacy 'ccis')
#
# Key Upgrades & Bug Fixes from Original:
#   1. Writes directly to the external Backup Appliance (APPLIANCE_MOUNT), NEVER
#      to the local CCIS server root '/' disk.
#   2. Verifies APPLIANCE_MOUNT resides on a non-root mounted filesystem before
#      running (works for mount roots and subdirectories alike).
#   3. Explicitly skips the retired/legacy 'ccis' stack (EXCLUDE_STACKS="ccis")
#      moving forward after its one-time archive.
#   4. Fixes bug in original script where a failed pg_dumpall still added the DB
#      volume to DB_VOLS (which caused BOTH the SQL dump and volume tar to be lost).
#   5. Verifies gzip integrity of each database dump and handles file paths with
#      spaces safely.
#
# Install:  /home/techsupport/odkx-prod/scripts/all-stacks-weekly-backup.sh (chmod 750)
# Schedule: /etc/cron.d/all-stacks-backup (0 0 * * 0 root ...)
# ==============================================================================

set -uo pipefail

# --- CONFIGURATION ---
APPLIANCE_MOUNT="${APPLIANCE_MOUNT:-/nfsdata/backup}"
BACKUP_ROOT="${APPLIANCE_MOUNT}/all-stacks"
EXCLUDE_STACKS="${EXCLUDE_STACKS:-ccis}"  # Do NOT back up legacy 'ccis' stack moving forward
KEEP_DAYS=56                              # keep 8 weekly backups on appliance
MIN_FREE_GB=30
COMPOSE_SEARCH="/home /opt /srv /root"
STAMP="$(date +%Y%m%d_%H%M)"
DEST="$BACKUP_ROOT/$STAMP"
LOG="$BACKUP_ROOT/backup.log"
ERRORS=0

err() { echo "ERROR: $*"; ERRORS=$((ERRORS+1)); }

# 0. Verify Backup Appliance is mounted (Never write to local root '/')
CHECK_PATH="$APPLIANCE_MOUNT"
while [ ! -d "$CHECK_PATH" ] && [ "$CHECK_PATH" != "/" ]; do
  CHECK_PATH="$(dirname "$CHECK_PATH")"
done
TARGET_MNT="$(df -P "$CHECK_PATH" 2>/dev/null | awk 'NR==2 {print $6}')"
if [ -z "$TARGET_MNT" ] || [ "$TARGET_MNT" = "/" ]; then
  echo "ERROR: Backup appliance '$APPLIANCE_MOUNT' resolves to local root '/'! Refusing to write to local disk." >&2
  exit 1
fi

mkdir -p "$DEST" || { echo "ERROR: Cannot create $DEST" >&2; exit 1; }
exec >>"$LOG" 2>&1
echo "===== $(date -u '+%F %T UTC') all-stacks backup start -> $DEST (Appliance Mount: $TARGET_MNT, excluding: $EXCLUDE_STACKS)"

FREE_GB="$(df -BG --output=avail "$DEST" | tail -1 | tr -dc '0-9')"
if [ -z "$FREE_GB" ] || [ "$FREE_GB" -lt "$MIN_FREE_GB" ]; then
  echo "ERROR: only ${FREE_GB:-0}G free on appliance $BACKUP_ROOT (need ${MIN_FREE_GB}G)"
  echo "===== FAILED"
  exit 1
fi

for STACK in $(docker stack ls --format '{{.Name}}'); do
  if [ -z "$STACK" ]; then
    continue
  fi
  # Skip legacy 'ccis' stack (already archived once and retired)
  if echo " $EXCLUDE_STACKS " | grep -q " $STACK "; then
    echo "--- skipping excluded legacy stack: $STACK"
    continue
  fi

  D="$DEST/$STACK"
  mkdir -p "$D/services" "$D/volumes" "$D/configs" "$D/compose" || { err "mkdir $D"; continue; }
  echo "--- stack: $STACK"

  # 1. Service definitions
  for S in $(docker stack services "$STACK" --format '{{.Name}}'); do
    if [ -z "$S" ]; then
      continue
    fi
    docker service inspect "$S" > "$D/services/$S.json" || err "inspect $S"
  done

  # 2. Databases: dump every running Postgres/PostGIS container of this stack
  DB_VOLS=""
  while IFS='|' read -r CID CNAME IMG; do
    if [ -z "$CID" ]; then
      continue
    fi
    if echo "$IMG" | grep -qiE 'postgres|postgis'; then
      SVC="${CNAME%%.*}"
      DUMP_GZ="$D/${SVC}_pg_dumpall.sql.gz"
      if docker exec "$CID" sh -c 'pg_dumpall -U "${POSTGRES_USER:-postgres}"' | gzip > "$DUMP_GZ" \
         && [ -s "$DUMP_GZ" ] && gzip -t "$DUMP_GZ" 2>/dev/null; then
        echo "DB OK: $SVC ($(du -h "$DUMP_GZ" | cut -f1))"
        # Only skip raw volume tar if pg_dumpall actually succeeded and passed gzip verification!
        DB_VOLS="$DB_VOLS $(docker inspect -f '{{range .Mounts}}{{.Name}} {{end}}' "$CID")"
      else
        rm -f "$DUMP_GZ"
        err "pg_dumpall $SVC"
      fi
    fi
  done < <(docker ps --filter "label=com.docker.stack.namespace=$STACK" --format '{{.ID}}|{{.Names}}|{{.Image}}')

  # 3. Other named volumes (LDAP, uploads, etc.)
  for V in $(docker volume ls -q --filter "label=com.docker.stack.namespace=$STACK"); do
    if [ -z "$V" ]; then
      continue
    fi
    if echo " $DB_VOLS " | grep -q " $V "; then
      echo "skip $V (DB volume - covered by verified dump)"
      continue
    fi
    MP="$(docker volume inspect -f '{{.Mountpoint}}' "$V")"
    tar czf "$D/volumes/$V.tar.gz" -C "$MP" . || err "tar $V"
  done

  # 4. Swarm configs used by the stack
  for C in $(docker config ls --filter "label=com.docker.stack.namespace=$STACK" --format '{{.Name}}'); do
    if [ -z "$C" ]; then
      continue
    fi
    docker config inspect -f '{{json .Spec.Data}}' "$C" | tr -d '"' | base64 -d > "$D/configs/$C" 2>/dev/null || err "config $C"
  done

  # 5. Compose files whose path mentions the stack name (safe line-by-line read)
  while IFS= read -r F; do
    if [ -z "$F" ]; then
      continue
    fi
    case "$F" in
      *"$STACK"*)
        # Avoid matching 'ccisprod' against 'ccis'
        if [ "$STACK" = "ccis" ] && [[ "$F" == *ccisprod* ]]; then
          continue
        fi
        cp --parents "$F" "$D/compose/" 2>/dev/null || true
        ;;
    esac
  done < <(find $COMPOSE_SEARCH -maxdepth 5 \( -name 'docker-compose*.yml' -o -name 'docker-compose*.yaml' -o -name 'stack*.yml' \) -not -path '*/node_modules/*' 2>/dev/null || true)
done

# ccisprod compose lives in odkx-prod (name does not contain "ccisprod")
if [ -d "$DEST/ccisprod/compose" ] && [ -f /home/techsupport/odkx-prod/docker-compose.yml ]; then
  cp --parents /home/techsupport/odkx-prod/docker-compose.yml "$DEST/ccisprod/compose/" 2>/dev/null || true
fi

( cd "$DEST" && find . -type f ! -name SHA256SUMS -exec sha256sum {} + > SHA256SUMS ) || err "sha256sum generation failed"
find "$BACKUP_ROOT" -mindepth 1 -maxdepth 1 -type d -mtime +"$KEEP_DAYS" -print -exec rm -rf {} + || echo "WARN: retention cleanup warning"

echo "Total size: $(du -sh "$DEST" | cut -f1)"
if [ "$ERRORS" -eq 0 ]; then
  echo "===== $(date -u '+%F %T UTC') backup OK"
else
  echo "===== backup finished with $ERRORS ERROR(S)"
  exit 1
fi

# ---------------------------------------------------------------------------
# /etc/cron.d/all-stacks-backup  (server is UTC; 00:00 Sun UTC = 03:00 Sun Uganda,
# one hour after the ccisprod backup)
# 0 0 * * 0 root /home/techsupport/odkx-prod/scripts/all-stacks-weekly-backup.sh
# ---------------------------------------------------------------------------
