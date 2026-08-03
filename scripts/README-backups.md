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
3. Copy the three values it shows you **once**: Access Key ID, Secret Access
   Key, and the S3 endpoint URL (`https://<account_id>.r2.cloudflarestorage.com`).
4. On the VPS, run `rclone config` and create a remote named exactly `r2`:
   - Storage type: `s3`
   - Provider: `Cloudflare`
   - Access Key ID / Secret Access Key: from step 3
   - Endpoint: from step 3
   - Leave region blank, ACL: `private`
5. Verify: `rclone lsd r2:` should list `edusmart-backups` without error.
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
backup is a guess, not a backup. Last verified: 2026-08-04, local decrypt +
extract confirmed against a real backup (dump, volume archive, and
saved_stories all intact).

## Cron

`30 3 * * * /www/wwwroot/edusmart/scripts/backup.sh >> /etc/edusmart-backup/logs/backup.log 2>&1`

Check `/etc/edusmart-backup/logs/backup.log` periodically — a cron job that
fails silently for months is worse than no backup, because it creates false
confidence.
