# Database continuity and market-data quality runbook

Snapshot: 25 November 2026

This runbook protects the existing Render PostgreSQL data and records the
read-only checks performed while preparing the technical-analysis release.
It does not authorize a database cutover, service reconfiguration, or data import.

## 1. Current database position

The existing Render database is:

- Database: `nepse_ai`
- PostgreSQL: 18.6
- Plan: Free
- Render expiry shown in the dashboard: 19 December 2026, 15:44 UTC
- Public schema: 35 tables; approximately 9.7 MiB
- Migration revision: `0016_stock_instrument_type`

The Render source contains the user's application and portfolio records, but no
market-history rows at the time of the check:

| Table | Render row count |
| --- | ---: |
| `users` | 1 |
| `portfolio_accounts` | 2 |
| `portfolio_positions` | 1 |
| `portfolio_transactions` | 2 |
| `stocks`, `prices`, `nepse_index` | 0 |

The existing Neon project `nepse-ai-platform-db` has a PostgreSQL 18 main branch
and the same Alembic revision. The two databases have identical public-schema
column definitions (268 columns). Read-only count and checksum comparisons
confirmed that all four populated user/portfolio tables listed above match
between Render and Neon. The Neon project additionally contains 240 stocks,
357,583 price rows, 6,666 NEPSE Index rows and 97 ingestion-log rows.

This is a verified data match for the current portfolio tables, not permission
to change the Render service environment variables. Keep the Render database
available until the backup and the application cutover have both been
independently validated.

### External database access control

The current Render PostgreSQL allowlist is 0.0.0.0/0 (all public IPv4
addresses). Render documents this as the default external-access rule; valid
database credentials are still required. Restrict it to known trusted CIDR
ranges where possible, and avoid leaving broad public access in place for a
production database:
https://render.com/docs/postgresql-creating-connecting

Render services in the same workspace and region can use the database's
internal connection path; Render documents that the external IP allowlist
does not apply to same-region private-network connections. Before narrowing
the rule, confirm which database URL the deployed API actually uses and
preserve a tested backup/restore route:
https://render.com/tutorials/postgres-on-render/connection-strings

No allowlist, connection URL, or service setting was changed in this PR because
the existing deployment configuration must be preserved. For the one-time
local backup, prefer temporarily allowing only the current trusted public IP
instead of relying on 0.0.0.0/0, then restore the intended allowlist.

## 2. Make an independent backup of Render PostgreSQL

Render's Free PostgreSQL plan does not provide managed backups or point-in-time
recovery. A free database becomes inaccessible when it expires. Render's
documentation describes a 14-day period after expiry during which an upgrade
can still prevent deletion, but do not use that grace period as a backup plan:

- https://render.com/docs/free
- https://render.com/docs/postgresql-backups

### Windows PowerShell backup

A reusable helper is included at scripts/backup_render_postgres.ps1. From the
repository root, run it with the External Connection hostname shown by Render:

```powershell
.\scripts\backup_render_postgres.ps1 -HostName "<EXTERNAL_HOST_FROM_RENDER_CONNECT>"
```

It prompts securely for the password, saves a timestamped custom-format dump
under the current user's nepse-ai-backups folder, validates the archive with
pg_restore --list, and prints its SHA256 hash. It does not restore or modify the
remote database. The commands below are the equivalent manual procedure.

Install PostgreSQL 18 command-line tools (`pg_dump`, `pg_restore`, and `psql`).
In the Render dashboard, open the existing database and select **Connect**.
Use the **External Connection** host shown there. Do not paste the connection
URL into Git, a ticket, chat, or a source file.

```powershell
$ErrorActionPreference = "Stop"

New-Item -ItemType Directory -Force .\private-backups | Out-Null
pg_dump --version

# Enter the password in the secure Windows credential prompt.
$credential = Get-Credential -UserName "nepse" -Message "Render PostgreSQL password"
$env:PGPASSWORD = $credential.GetNetworkCredential().Password
$env:PGSSLMODE = "require"

$dump = ".\private-backups\nepse_ai_render_20261125.dump"

pg_dump `
  --host "<EXTERNAL_HOST_FROM_RENDER_CONNECT>" `
  --port 5432 `
  --username "nepse" `
  --dbname "nepse_ai" `
  --no-password `
  --format=custom `
  --no-owner `
  --no-privileges `
  --file $dump

if ($LASTEXITCODE -ne 0) { throw "pg_dump failed; do not proceed." }

# Confirm that PostgreSQL can read the archive's table-of-contents.
pg_restore --list $dump | Out-File ".\private-backups\nepse_ai_render_20261125.toc.txt"
if ($LASTEXITCODE -ne 0) { throw "Backup archive validation failed." }

Get-FileHash -Algorithm SHA256 $dump
Remove-Item Env:PGPASSWORD
Remove-Item Env:PGSSLMODE
```

Replace only the host placeholder with the external hostname shown by Render.
Do not put the password in the command itself. Keep the dump outside the
repository, protect it as sensitive account data, and retain a second copy in
a separate secure location.

`pg_restore --list` confirms the archive is readable. For a full restore test,
restore the dump into a new, empty local PostgreSQL 18 database and verify the
Alembic revision plus the four portfolio-table row counts. Do not restore into
the existing Neon database: it already contains application data.

## 3. Safe continuity and cutover sequence

1. Create and verify the independent Render dump.
2. Recheck the Render and Neon schema revision, column definitions, and
   checksums of `users`, `portfolio_accounts`, `portfolio_positions`, and
   `portfolio_transactions` immediately before cutover.
3. Check whether any new portfolio transactions or positions were added since
   the 25 November 2026 snapshot. If so, reconcile them before changing the
   application connection.
4. Validate the existing Neon database with an isolated application/API
   smoke test. Verify login, portfolio balances, positions, and transaction
   history using the same account.
5. Only after the checks pass and the change is explicitly approved should the
   API's database connection be changed. Keep the old Render database intact
   during the rollback window.
6. Recheck API health, dashboard access, portfolio records, and application
   logs after cutover. Do not delete or allow the source database to expire
   until the independent backup and target validation are complete.

The existing `scripts/migrate_render_to_neon.py` is intentionally limited to
the four user/portfolio tables. It checks schema revision, column equality,
source minimum row counts, and target checksums before copying; it refuses
overwrites when target data differs. Its current expected minimum counts are
met by the Render source. Because the four target table checksums already
match, do not run it merely for reassurance: the read-only checksum check has
already established equality. Re-run the check before any actual cutover.

## 4. Market-data rejects: keep validation strict

The public source CSVs were checked in memory using the importer's OHLCV rules.
No database connection or writes were performed.

| Symbol | Source rows | Valid unique sessions | Rejected rows |
| --- | ---: | ---: | ---: |
| ADBL | 3,682 | 3,277 | 405 |
| AHPC | 3,783 | 3,196 | 587 |

All 992 rejected ADBL/AHPC rows have OHLC range inconsistencies:

- ADBL: 270 rows have `high` below `open` or `close`; 135 have `low`
  above `open` or `close`; no row has `high < low`.
- AHPC: 348 rows have `high` below `open` or `close`; 241 have `low`
  above `open` or `close`; nine rows have `high < low`. The latter group
  overlaps the high-range category in five rows and the low-range category in
  six rows; two rows fall into all three categories. The total rejected-row
  count remains 587 for AHPC and 405 for ADBL.

No missing/unparseable date, missing/non-positive OHLC, negative-volume, or
duplicate-date issue was identified in this rejected set. The audit script
reports the three range reasons separately and still applies the importer's
same reject decision. Do not repair these values by swapping high/low or
substituting open/close. Keep them excluded until independently cross-checked
against a reliable exchange/source record.

A repeatable, read-only row-level audit utility is available:

```powershell
python scripts/audit_market_data_quality.py --symbols ADBL,AHPC --max-samples 20
```

The script reports rejection reasons and representative CSV line numbers. It
fetches the public CSV catalogue and the requested public CSV files only; it
does not connect to PostgreSQL or change data.

## 5. Existing Neon market-history readiness (read-only, 25 November 2026)

The existing Neon database contains a usable market-history foundation, but it
is not yet complete enough to treat every symbol and every latest-session
composite signal as current:

- 240 listed ordinary-share symbols have price history. The current source
  catalogue contains 285 listed ordinary shares, leaving 45 symbols absent
  from the database.
- The 357,583 stored price rows span 1995-07-20 through 2026-11-24. The 6,666
  stored NEPSE Index rows span 1997-07-20 through 2026-11-23.
- 208 symbols have price rows dated 2026-11-24; the NEPSE Index source CSV
  already contains a valid 2026-11-24 row, but that row is not yet in the
  database. The latest stored benchmark is therefore one session behind the
  latest stored stock prices.
- The technical-analysis API suppresses its current composite BUY/SELL score
  whenever the latest stock and benchmark dates differ. Indicator history is
  still available. This is intentional: it prevents using a mismatched market
  regime as same-session confirmation.
- A read-only OHLCV profile of stored prices found no null/non-positive OHLC,
  impossible high/low ranges, null volume, or negative volume. The database
  enforces a unique (stock_id, date) key for prices.
- Four stored symbols are stale relative to the 25 November snapshot:
  CFL (latest 2011-07-03), CMB (2019-05-21), KRBL (2024-12-24), and NLO
  (2026-09-30). Do not silently mark them current; verify source listing
  status and whether a newer source record exists before using or deactivating.
- The data_trust, features, signals, backtests, models, and trades tables
  currently contain no rows. The presence of raw history alone does not
  mean feature engineering, signal history, model training or historical
  strategy validation has been completed.

### Missing-symbol source-file audit

All 45 symbols absent from Neon were checked against the public source CSVs
in memory. No database connection or writes were made. The files contain
47,530 source rows; 45,720 pass the current importer OHLCV range checks and
1,810 are rejected (3.81%). No invalid-date, missing/non-positive OHLC,
negative-volume or duplicate-valid-date issue was found in this batch. The
missing symbols are: SMHL, SMJC, SMPDA, SNLI, SNORL, SOHL, SONA, SOPL, SPC,
SPDL, SPHL, SPIL, SPL, SRLI, SSHL, STC, SWASTIK, SWBBL, SWMF, SYPNL, TAMOR,
TPC, TPKHL, TRH, TSHL, TTL, TVCL, UAIL, UHEWA, ULBSL, ULHC, UMHL, UMRH,
UNHPL, UNL, UNLB, UPCL, UPPER, USHEC, USHL, USLB, VLBS, VLUCL, WNLB, YMHL.
Rejected rows are concentrated in SWBBL (633), TRH (491), UNL (362),
VLBS (180), SPDL (46), UMHL (43), and STC (33). The remaining source rejects
are smaller counts across SONA, SPHL, SPIL, SPL, SRLI, SWASTIK, SWMF, TVCL,
UAIL, ULBSL, UNHPL, UPCL, USHL, USLB, VLUCL and WNLB. These files must still
be passed through the repeatable row-level audit and validated again at
import time. WNLB's latest available source row is 2026-09-03; verify its
freshness before importing.

Do not loosen OHLCV rules, silently repair rejected values, or interpret
unlisted/stale files as current. A controlled market-data import should be
separately planned, run in bounded batches, and checked against counts,
duplicates, freshness and source rejects. It is not authorized by this PR.

## 6. Release boundary

- PR #12 remains a draft until review is complete.
- The Render API and dashboard remain on `main`.
- No Render environment variables, service settings, database rows, or schema
  migrations are changed by this runbook.
- Do not run the live importer against the Render source as part of release
  preparation. Market-data import should occur only after the target/source
  boundary, quality rejects, and rollback plan are approved.
