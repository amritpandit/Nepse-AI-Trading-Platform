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

   The script prompts for the URL using hidden secure input. Alternatively, set the RENDER_DATABASE_URL environment variable in the current PowerShell session before running it. Do not write the URL into a file or commit it.
4. The script stores a custom-format archive, a SHA-256 file, a pg_restore --list output and a manifest under:

   ```
   %LOCALAPPDATA%\NepseAI\database-backups
   ```

5. Verify the archive, hash and manifest exist. Recompute the checksum with `Get-FileHash -Algorithm SHA256 "<archive-path>"` and compare it with the `.sha256` file. Copy the `.dump` archive and `.sha256` file to a second encrypted location (for example, an encrypted external drive). Keep the copy outside this Git repository and do not upload it to GitHub.
6. Treat a successful pg_restore --list as an archive-read check, **not** as proof that a full restore works. Complete the restore rehearsal below.

The archive contains the entire application database, including user and portfolio records and password hashes. It does not include cluster-wide roles or other databases. Protect it as sensitive data.

The script parses the external URL locally, creates an ACL-restricted temporary directory for the pgpass file, and removes the directory on exit. The URL and password are not printed or placed in pg_dump process arguments or the backup manifest. The script never writes to the database and never changes Render settings. The custom-format archive itself is not encrypted; store it only on a BitLocker/EFS-protected drive and a second encrypted location.

## Restore rehearsal (separate, empty database only)

Do not restore over the live production database.

1. Provision a separate, empty PostgreSQL 18 database. A local PostgreSQL 18 instance is suitable. Copy its connection URL into a hidden PowerShell prompt; do not paste the URL into chat, GitHub, issue comments, logs or a plain-text file.
2. Use the same credential-handling approach as the backup script: parse the target URL, put the password in a temporary pgpass file with access restricted to your Windows account, and set `PGPASSFILE` and `PGSSLMODE` in the current PowerShell process. Never put the password in the `pg_restore` command line.
3. Before restoring, use `psql` with the target host, port, database name and username (but no password argument) to run:

   ```sql
   SELECT COUNT(*)
   FROM information_schema.tables
   WHERE table_schema NOT IN ('pg_catalog', 'information_schema')
     AND table_type = 'BASE TABLE';
   ```

   Continue only when the result is **0**. If the target is not empty, stop and provision a fresh empty database instead.
4. Recompute and compare the archive SHA-256. Then run `pg_restore` with `--exit-on-error --verbose --no-owner --no-privileges`, supplying the target host, port, database name and username as separate non-secret arguments. Do not use a production connection URL as the `--dbname` argument.
5. Compare table names, row counts, migration version, portfolio account/position/transaction counts and user count with the source inventory. Do not print or share user credentials, password hashes, tokens or personal portfolio details.
6. Keep the restored database isolated. Do not point the running Render services at it unless a separate cutover has been planned and explicitly approved.


## Recovery / cutover rule

A backup is not considered verified until a restore rehearsal succeeds and the SHA-256 checksum matches. Before any future migration, deploy or database cutover:

- create a fresh backup;
- verify the archive and checksum;
- rehearse restore into an empty target;
- document the exact source/target and rollback plan;
- only then schedule any controlled cutover.

This runbook deliberately does not create a new database, change the current database plan, alter service environment variables, run a restore, or change Render configuration.
