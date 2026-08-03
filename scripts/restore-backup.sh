#!/usr/bin/env bash
# Decrypt + unpack a backup produced by backup.sh, without touching anything
# live. Verifies the archive is actually restorable; does NOT restore INTO
# the running MySQL/volume/filesystem by itself - review contents first.
#
# Usage: ./restore-backup.sh /path/to/edusmart_backup_TIMESTAMP.tar.gpg [output_dir]
set -euo pipefail

ARCHIVE="${1:?Usage: restore-backup.sh <archive.tar.gpg> [output_dir]}"
OUT_DIR="${2:-./restore_$(date +%Y%m%d_%H%M%S)}"
PASSPHRASE_FILE="/etc/edusmart-backup/passphrase"

[ -f "$ARCHIVE" ] || { echo "Archive not found: $ARCHIVE"; exit 1; }
[ -f "$PASSPHRASE_FILE" ] || { echo "Passphrase file not found: $PASSPHRASE_FILE"; exit 1; }

mkdir -p "$OUT_DIR"
echo "Decrypting..."
gpg --batch --yes --decrypt --passphrase-file "$PASSPHRASE_FILE" "$ARCHIVE" \
  | tar xf - -C "$OUT_DIR"

echo "Extracted to $OUT_DIR:"
ls -la "$OUT_DIR"

echo
echo "Verifying MySQL dump is well-formed SQL..."
head -5 "$OUT_DIR/mysql_dump.sql"
grep -q "^-- MySQL dump" "$OUT_DIR/mysql_dump.sql" && echo "OK: looks like a valid mysqldump."

echo
echo "Verifying job_state volume archive..."
tar tzf "$OUT_DIR/job_state_volume.tar.gz" | head -10 || true

echo
echo "Verifying saved_stories archive..."
tar tzf "$OUT_DIR/saved_stories.tar.gz" | head -10 || true

echo
echo "Done. Nothing live was touched. To actually restore:"
echo "  MySQL:   docker exec -i <mysql-container-or-host> mysql -u USER -p DB < $OUT_DIR/mysql_dump.sql"
echo "  Volume:  docker run --rm -v edusmart_backend_db:/vol -v $OUT_DIR:/backup alpine \\"
echo "             sh -c 'rm -rf /vol/* && tar xzf /backup/job_state_volume.tar.gz -C /vol'"
echo "  Stories: tar xzf $OUT_DIR/saved_stories.tar.gz -C backend/"
echo "  .env:    cp $OUT_DIR/dot_env .env   # review before overwriting a live one"
