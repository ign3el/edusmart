# EduSmart offsite backups

## What's covered
`.env` (30 live secrets), MySQL (`mysqldump`), the `edusmart_backend_db` Docker
volume (job_state.db, spend counters, hash cache), and `saved_stories/`.
`generated_stories/` is deliberately excluded — 24h TTL, disposable.

## How it works
`scripts/backup.sh` dumps everything into a tar, encrypts it with `gpg`
(AES256, passphrase-based) using the passphrase at
`/etc/edusmart-backup/passphrase` (mode 600, outside the repo, outside every
Docker mount), then uploads the encrypted archive to Cloudflare R2 via
`rclone`. If no R2 remote is configured yet, the archive stays local at
`/etc/edusmart-backup/staging/` and the script prints a warning — it does
NOT fail silently.

**The passphrase must also live in your password manager**, not just on this
VPS. If this box is lost with only the local copy of the passphrase, every
offsite backup becomes unreadable ciphertext forever. This was generated and
shown once during setup on 2026-08-04 — it is not retrievable from this repo
or from Claude's memory.

## One-time setup — Cloudflare R2 (you have to do this part)

1. Cloudflare dashboard → **R2** → **Create bucket** → name it
   `edusmart-backups`. Free tier: 10 GB storage, this project uses ~50 MB/backup.
2. **R2** → **Manage API tokens** → **Create API token** → permission
   **Object Read & Write**, scoped to the `edusmart-backups` bucket only
   (not account-wide).
3. Copy the two values it shows you **once**: Access Key ID and Secret Access
   Key. Use the **S3 API** endpoint from the bucket's General page
   (`https://<account_id>.r2.cloudflarestorage.com` — the account-level one,
   NOT with `/edusmart-backups` appended, and NOT a jurisdiction-specific
   `.eu.`/etc. variant even if the bucket's Location shows a specific region
   like "Eastern Europe" - the plain account endpoint is correct regardless).
4. On the VPS, create the remote non-interactively (cleaner than the
   interactive wizard - real values only, no leftover fields):
   ```bash
   rclone config create r2 s3 \
     provider=Cloudflare \
     access_key_id=YOUR_ACCESS_KEY_ID \
     secret_access_key=YOUR_SECRET_ACCESS_KEY \
     endpoint=https://YOUR_ACCOUNT_ID.r2.cloudflarestorage.com \
     region=auto \
     no_check_bucket=true
   ```
   **`no_check_bucket=true` is required, not optional.** Without it every
   write 403s (AccessDenied) even with fully correct, confirmed-correct
   credentials - discovered and root-caused 2026-08-04 after ruling out
   bucket lock rules, billing status, jurisdiction endpoints, and a clean
   config recreate. Cause: rclone's S3 backend does a bucket-level
   HeadBucket-style check before every write by default, and a token
   correctly scoped to Object Read & Write (not bucket-level admin) can't
   pass that check - so the check itself gets denied and rclone aborts
   before ever attempting the actual object PUT. `no_check_bucket=true`
   skips that check, which is safe here since the bucket already exists.
5. Verify with a real write, not just a listing - `rclone lsd r2:` (no
   bucket) 403s even when everything is correctly configured, because a
   bucket-scoped token can't list the whole account. Use:
   ```bash
   echo test > /tmp/r2test.txt && rclone copyto /tmp/r2test.txt r2:edusmart-backups/r2test.txt \
     && rclone delete r2:edusmart-backups/r2test.txt && rm /tmp/r2test.txt
   ```
   **A `501 Not Implemented` on the first attempt, then "Attempt 2/3
   succeeded," is normal** - rclone retries with a different method and it
   works. Only a final failure after all 3 attempts is a real problem.
6. Run `./scripts/backup.sh` again — it will now upload automatically.

## Restore

```bash
./scripts/restore-backup.sh /etc/edusmart-backup/staging/edusmart_backup_TIMESTAMP.tar.gpg
```

or pull one down from R2 first: `rclone copy r2:edusmart-backups/edusmart_backup_TIMESTAMP.tar.gpg .`

This only decrypts and verifies — it will NOT overwrite live MySQL, the
Docker volume, or `saved_stories/` on its own. The script prints the exact
commands to do that manually; review the extracted contents first.

**Test the restore path periodically** (e.g. quarterly) — an unrestored
backup is a guess, not a backup. Last verified: 2026-08-04 — full pipeline
including the real R2 upload: `./scripts/backup.sh` produced a 46MB encrypted
archive, confirmed present in `r2:edusmart-backups/` at the exact matching
byte size, and separately, local decrypt + extract confirmed the dump, volume
archive, and saved_stories were all intact inside it.

## Cron

`30 3 * * * /www/wwwroot/edusmart/scripts/backup.sh >> /etc/edusmart-backup/logs/backup.log 2>&1`

Check `/etc/edusmart-backup/logs/backup.log` periodically — a cron job that
fails silently for months is worse than no backup, because it creates false
confidence.
