# Render PostgreSQL backup and recovery runbook

## Current database facts

The existing Render database is the primary database for this project. At the time this runbook was prepared, Render showed:

- Instance: nepse-database
- PostgreSQL major version: 18
- Compute plan: Free
- Expiration: **19 December 2026**
- Region: Singapore

The live database contains user and portfolio records even though the stock/price/feature/signal/backtest tables are currently empty. Treat the database and every backup as confidential.

## Why an external backup is required

Render's current documentation states that Free Postgres instances do not receive Render-managed logical exports or point-in-time recovery. Free databases expire 30 days after creation, become inaccessible at expiry unless upgraded, and are deleted after the 14-day grace period. Do not rely on the grace period as a backup strategy.

References:
- https://render.com/docs/postgresql-backups
- https://render.com/docs/free

## Create a local logical backup (Windows)

1. Install PostgreSQL **18** command-line tools (pg_dump and pg_restore) on the Windows machine. Add their bin directory to PATH and reopen PowerShell.
2. In the Render Dashboard, open nepse-database → **Info** (or Connect) and copy the **External Database URL**. Do not use the internal URL from outside Render.
3. From the repository root, run:

   ```powershell
   .\scripts\backup_render_postgres.ps1
   ```

   The script always prompts for the external URL using hidden secure input. Do not paste the URL into chat, store it in a file, or commit it.
4. The script stores a custom-format archive, a SHA-256 file, a pg_restore --list output and a manifest under:

   ```
   %LOCALAPPDATA%\NepseAI\database-backups
   ```

5. Verify the archive, hash and manifest exist. Recompute the checksum with `Get-FileHash -Algorithm SHA256 "<archive-path>"` and compare it with the `.sha256` file. Copy the `.dump` archive and `.sha256` file to a second encrypted location (for example, an encrypted external drive). Keep the copy outside this Git repository and do not upload it to GitHub.
6. Treat a successful pg_restore --list as an archive-read check, **not** as proof that a full restore works. Complete the restore rehearsal below.

The archive contains the entire application database, including user and portfolio records and password hashes. It does not include cluster-wide roles or other databases. Protect it as sensitive data.

The backup and restore helpers parse connection URLs locally, create ACL-restricted temporary directories for pgpass files, and remove those directories on exit. URLs and passwords are not printed or placed in PostgreSQL process arguments or backup manifests. The backup helper is read-only against its source. The restore helper writes only to a separate non-Render target after it confirms that target has no application objects and receives typed confirmation. The custom-format archive itself is not encrypted; store it only on a BitLocker/EFS-protected drive and a second encrypted location.

## Restore rehearsal (separate, empty database only)

Do not restore over the live production database.

1. Install PostgreSQL 18 command-line tools, including `pg_restore` and `psql`.
2. Provision a **separate, empty local/test PostgreSQL 18 database**. Do not use a Render database URL. The restore helper refuses Render hosts.
3. Recompute and compare the archive SHA-256 with the sidecar created by the backup script.
4. From the repository root, run:

   ```powershell
   .\scripts\restore_postgres_to_empty_db.ps1 -ArchivePath "C:\\path\\to\\nepse-ai-postgres-YYYYMMDD-HHMMSS.dump"
   ```

5. The helper prompts for the target URL using hidden input, creates a temporary ACL-restricted pgpass directory, verifies the archive hash, checks that the target has zero application relations, and requires you to type exactly `RESTORE TO EMPTY DATABASE` before it invokes `pg_restore`. If the database is not empty, it stops without restoring.
6. After a successful restore, compare table names, row counts, migration version, portfolio account/position/transaction counts and user count with the source inventory. Do not print or share user credentials, password hashes, tokens or personal portfolio details.
7. Keep the restored database isolated. Do not point running Render services at it unless a separate cutover has been planned and explicitly approved.


## Recovery / cutover rule

A backup is not considered verified until a restore rehearsal succeeds and the SHA-256 checksum matches. Before any future migration, deploy or database cutover:

- create a fresh backup;
- verify the archive and checksum;
- rehearse restore into an empty target;
- document the exact source/target and rollback plan;
- only then schedule any controlled cutover.

This runbook deliberately does not create a new database, change the current database plan, alter service environment variables, run a restore, or change Render configuration.
