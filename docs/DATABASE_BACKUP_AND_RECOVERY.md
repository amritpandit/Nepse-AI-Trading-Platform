# PostgreSQL backup and recovery runbook

## Active database and current recovery posture

The active application database for the private NEPSE AI Trading Platform is **Neon**, not the older Render PostgreSQL instance.

- Neon project: `nepse-ai-platform-db`
- Active branch: `main`
- Database: `nepse_ai`
- PostgreSQL major version: **18**
- Region: Singapore
- Current production data includes market history, user records and portfolio records. Treat all database exports as confidential.

The older Render PostgreSQL instance belongs to the legacy deployment and is not the source of truth for the private platform. Do not point the private API or dashboard at it. A legacy Render archive, if ever required, must be handled as a separate preservation task.

The following Neon recovery controls were verified on **25 November 2026**:

- Neon Free point-in-time history was enabled with a **6-hour** retention window.
- One manual snapshot exists: `pre-portfolio-migration-2026-11-24`.
- An in-project, no-compute recovery branch named `recovery-anchor-2026-11-25` was created from the active main branch.
- Scheduled snapshot creation is not available for this Free project, and the project currently has no automatic snapshot schedule.

These are useful recovery layers, but they are **not an off-site logical backup**. A recovery branch is inside the same Neon project and does not protect against loss of that project or account. The six-hour history window is short. Keep a separately stored logical export as the durable backup layer.

## Create an external logical backup (Windows)

1. Install PostgreSQL **18** command-line tools (`pg_dump` and `pg_restore`) on the Windows machine. Add their bin directory to PATH and reopen PowerShell.
2. In the Neon Console, open the `nepse-ai-platform-db` project and use **Connect** for the active `main` branch and `nepse_ai` database. Select the **Direct** (non-pooled) connection string. Do not use a pooled URL for `pg_dump`.
3. From the repository root, run:

   ```powershell
   .\\scripts\\backup_postgres.ps1
   ```

   The script prompts for the direct PostgreSQL URL using hidden secure input. Do not paste the URL into chat, store it in a file, or commit it. The helper is provider-neutral, but the active-platform backup should target the Neon direct connection.
4. The script stores a PostgreSQL custom-format archive, a SHA-256 file, a `pg_restore --list` output and a non-secret manifest under:

   ```
   %LOCALAPPDATA%\\NepseAI\\database-backups
   ```

5. Verify the archive, hash and manifest exist. Recompute the checksum with `Get-FileHash -Algorithm SHA256 "<archive-path>"` and compare it with the `.sha256` file. Copy the `.dump` archive and `.sha256` file to a second encrypted location (for example, an encrypted external drive or approved encrypted cloud storage). Keep the copy outside this Git repository and do not upload it to GitHub.
6. Treat a successful `pg_restore --list` as an archive-read check, **not** as proof that a full restore works. Complete the local restore rehearsal below.

The archive contains the application database, including user and portfolio records and password hashes. It does not include cluster-wide roles or other databases. Protect it as sensitive data.

The backup helper parses the connection URL locally, creates an ACL-restricted temporary directory for the `pgpass` file, and removes that directory on exit. The URL and password are not printed or placed in PostgreSQL process arguments or the backup manifest. The helper is read-only against its source. The custom-format archive itself is **not encrypted**; store it only on an encrypted drive and a second encrypted location.

## Local restore rehearsal (localhost only)

Never restore over the live Neon database or the legacy Render database. The restore helper now accepts **localhost/loopback targets only** and rejects remote hosts, including Neon and Render.

1. Install PostgreSQL 18 command-line tools, including `pg_restore` and `psql`.
2. Provision a **separate, empty local PostgreSQL 18 test database** on the Windows machine. Do not use a Neon, Render, or other remote database URL.
3. Recompute and compare the archive SHA-256 with the sidecar created by the backup script.
4. From the repository root, run:

   ```powershell
   .\\scripts\\restore_postgres_to_empty_db.ps1 -ArchivePath "C:\\path\\to\\nepse-ai-postgres-YYYYMMDD-HHMMSS.dump"
   ```

5. The helper prompts for the local target URL using hidden input, creates a temporary ACL-restricted `pgpass` directory, verifies the archive hash, checks that the target has zero application relations, routines and non-default schemas, and requires you to type exactly `RESTORE TO EMPTY DATABASE` before it invokes `pg_restore`. If the database is not empty, it stops without restoring. The script also refuses any non-local host.
6. After a successful restore, compare table names, row counts, migration version, portfolio account/position/transaction counts and user count with the source inventory. Do not print or share user credentials, password hashes, tokens or personal portfolio details.
7. Keep the restored database isolated. Do not point running Render services or the private platform at it.

## Backup acceptance and change-control rule

A backup is not considered fully verified until all of the following are completed:

- a fresh logical export from the active Neon direct connection;
- a successful `pg_restore --list` archive-read check;
- SHA-256 verification;
- a copy in a second encrypted location;
- a successful full restore rehearsal into a separate local empty database;
- a documented source/target inventory and rollback plan.

Before any future migration, deploy or database cutover, complete the same backup checks first. No database reset, destructive migration, data import, restore or production cutover is authorized by this runbook.

**Current completion status:** Neon six-hour history and an in-project recovery branch are enabled/created. The external logical export and local restore rehearsal have **not yet been executed** on the operator's Windows machine. Do not report the backup as fully verified until those steps pass.
