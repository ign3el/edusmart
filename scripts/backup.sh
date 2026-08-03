#!/usr/bin/env bash
# Offsite encrypted backup for EduSmart.
#
# Covers everything that exists in exactly one place on this VPS:
#   - .env (30 live secrets - JWT, Stripe, Gemini/Groq/RunPod keys)
#   - MySQL (users, stories, billing)
#   - the job_state Docker volume (job_state.db, runpod_usage.json spend
#     counter, hash_cache.json) - NOT the bind-mounted backend/db_data on
#     the host, which is empty; the real data lives in the named volume
#   - saved_stories/ (user content; generated_stories/ is 24h-TTL and
#     deliberately excluded - it's disposable)
#
# Encrypted with gpg (AES256, passphrase-based) before it ever leaves the
# box, because the archive contains live secrets. The passphrase lives at
# /etc/edusmart-backup/passphrase (600, outside the repo, outside any
# Docker mount) - copy it into a password manager too. If this VPS is lost
# with only the local copy, the offsite backups are unrecoverable ciphertext.
set -euo pipefail

PROJECT_DIR="/www/wwwroot/edusmart"
BACKUP_ROOT="/etc/edusmart-backup"
PASSPHRASE_FILE="$BACKUP_ROOT/passphrase"
STAGING_DIR="$BACKUP_ROOT/staging"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
ARCHIVE_NAME="edusmart_backup_${TIMESTAMP}"
RETAIN_DAYS=30
LOG_TAG="edusmart-backup"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }
fail() { log "ERROR: $*"; exit 1; }

[ -f "$PASSPHRASE_FILE" ] || fail "passphrase file missing: $PASSPHRASE_FILE"
[ "$(stat -c %a "$PASSPHRASE_FILE")" = "600" ] || fail "passphrase file must be mode 600"

mkdir -p "$STAGING_DIR"
WORKDIR="$(mktemp -d "$STAGING_DIR/${ARCHIVE_NAME}.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

cd "$PROJECT_DIR"

# 1. .env — the only copy of 30 live secrets.
[ -f .env ] || fail ".env not found at $PROJECT_DIR/.env"
cp .env "$WORKDIR/dot_env"

# 2. MySQL dump.
export MYSQL_PWD
MYSQL_PWD="$(grep -E '^MYSQL_PASSWORD=' .env | cut -d= -f2-)"
MYSQL_HOST_V="$(grep -E '^MYSQL_HOST=' .env | cut -d= -f2-)"
MYSQL_PORT_V="$(grep -E '^MYSQL_PORT=' .env | cut -d= -f2-)"
MYSQL_USER_V="$(grep -E '^MYSQL_USER=' .env | cut -d= -f2-)"
MYSQL_DB_V="$(grep -E '^MYSQL_DATABASE=' .env | cut -d= -f2-)"
log "Dumping MySQL ($MYSQL_DB_V @ $MYSQL_HOST_V)..."
# edusmart-backend is a Python app image with no mysql client - use a
# throwaway official mysql image instead (multi-arch, works on ARM).
docker run --rm -e MYSQL_PWD="$MYSQL_PWD" mysql:8 \
  mysqldump -h "$MYSQL_HOST_V" -P "$MYSQL_PORT_V" -u "$MYSQL_USER_V" \
  --single-transaction --routines --triggers "$MYSQL_DB_V" \
  > "$WORKDIR/mysql_dump.sql" \
  || fail "mysqldump failed"
unset MYSQL_PWD
[ -s "$WORKDIR/mysql_dump.sql" ] || fail "mysqldump produced an empty file"

# 3. job_state Docker named volume (job_state.db, runpod_usage.json, hash_cache.json).
log "Archiving edusmart_backend_db volume..."
docker run --rm -v edusmart_backend_db:/vol -v "$WORKDIR:/backup" alpine \
  tar czf /backup/job_state_volume.tar.gz -C /vol . \
  || fail "volume archive failed"

# 4. saved_stories/ (user content, not TTL-cleaned).
log "Archiving saved_stories/..."
tar czf "$WORKDIR/saved_stories.tar.gz" -C "$PROJECT_DIR/backend" saved_stories \
  || fail "saved_stories archive failed"

# Bundle + encrypt.
log "Bundling and encrypting..."
FINAL_TAR="$STAGING_DIR/${ARCHIVE_NAME}.tar"
tar cf "$FINAL_TAR" -C "$WORKDIR" dot_env mysql_dump.sql job_state_volume.tar.gz saved_stories.tar.gz

ENCRYPTED_FILE="$STAGING_DIR/${ARCHIVE_NAME}.tar.gpg"
gpg --batch --yes --symmetric --cipher-algo AES256 \
  --passphrase-file "$PASSPHRASE_FILE" \
  -o "$ENCRYPTED_FILE" "$FINAL_TAR" \
  || fail "gpg encryption failed"
rm -f "$FINAL_TAR"

SIZE_HUMAN="$(du -h "$ENCRYPTED_FILE" | cut -f1)"
log "Encrypted archive ready: $ENCRYPTED_FILE ($SIZE_HUMAN)"

# Upload offsite (Cloudflare R2 via rclone remote "r2").
if rclone listremotes 2>/dev/null | grep -q '^r2:'; then
  log "Uploading to r2:edusmart-backups/..."
  rclone copy "$ENCRYPTED_FILE" "r2:edusmart-backups/" --checksum \
    || fail "rclone upload failed"
  log "Upload complete."

  log "Pruning offsite backups older than ${RETAIN_DAYS}d..."
  rclone delete "r2:edusmart-backups/" --min-age "${RETAIN_DAYS}d" || true
else
  log "WARNING: no rclone remote named 'r2' configured — backup stayed LOCAL ONLY at $ENCRYPTED_FILE."
  log "This defeats the point of an offsite backup. See scripts/README-backups.md to finish setup."
fi

# Local retention (staging dir also acts as a short local cache).
find "$STAGING_DIR" -maxdepth 1 -name 'edusmart_backup_*.tar.gpg' -mtime +7 -delete

log "Backup finished: $ARCHIVE_NAME"
