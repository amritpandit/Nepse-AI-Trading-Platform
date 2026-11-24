[CmdletBinding()]
param(
    [string]$BackupDirectory = (Join-Path $env:LOCALAPPDATA "NepseAI\database-backups")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Get-PostgresTool {
    param([Parameter(Mandatory = $true)][string]$Name)

    $command = Get-Command $Name -ErrorAction SilentlyContinue
    if (-not $command) {
        throw "$Name was not found. Install PostgreSQL 18 command-line tools and reopen PowerShell."
    }
    return $command.Source
}

$pgDump = Get-PostgresTool -Name "pg_dump"
$pgRestore = Get-PostgresTool -Name "pg_restore"

$versionText = (& $pgDump --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0) {
    throw "Could not read pg_dump version."
}
if ($versionText -notmatch "PostgreSQL\) (?<major>\d+)") {
    throw "Could not parse pg_dump version: $versionText"
}
$clientMajor = [int]$Matches["major"]
if ($clientMajor -lt 18) {
    throw "This database runs PostgreSQL 18. Use pg_dump 18 or newer; detected: $versionText"
}

New-Item -ItemType Directory -Path $BackupDirectory -Force | Out-Null
$BackupDirectory = (Resolve-Path $BackupDirectory).Path

$timestamp = (Get-Date).ToUniversalTime().ToString("yyyyMMdd-HHmmss")
$archivePath = Join-Path $BackupDirectory "nepse-ai-postgres-$timestamp.dump"
$tocPath = Join-Path $BackupDirectory "nepse-ai-postgres-$timestamp.restore-list.txt"
$manifestPath = Join-Path $BackupDirectory "nepse-ai-postgres-$timestamp.manifest.json"
$hashPath = "$archivePath.sha256"

$secureUrl = $null
$plainUrl = $null
$ptr = [IntPtr]::Zero
$previousPgDatabase = $env:PGDATABASE

try {
    if ([string]::IsNullOrWhiteSpace($env:RENDER_DATABASE_URL)) {
        $secureUrl = Read-Host "Paste the Render EXTERNAL database URL (input is hidden)" -AsSecureString
        $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureUrl)
        $plainUrl = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
        $env:PGDATABASE = $plainUrl
    }
    else {
        $env:PGDATABASE = $env:RENDER_DATABASE_URL
    }

    if ([string]::IsNullOrWhiteSpace($env:PGDATABASE)) {
        throw "A database URL is required."
    }

    Write-Host "Starting a logical backup. The connection URL will not be printed or written to the manifest."
    Write-Host "Destination: $archivePath"

    & $pgDump --no-password --format=custom --no-owner --no-privileges --verbose --file $archivePath
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed with exit code $LASTEXITCODE. No successful backup is reported."
    }

    if (-not (Test-Path -LiteralPath $archivePath) -or (Get-Item -LiteralPath $archivePath).Length -eq 0) {
        throw "The backup archive is missing or empty."
    }

    & $pgRestore --list $archivePath 1> $tocPath
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $tocPath)) {
        throw "pg_restore could not read the archive directory. Treat this backup as invalid."
    }

    $fileHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    Set-Content -LiteralPath $hashPath -Value "$fileHash  $(Split-Path -Leaf $archivePath)" -Encoding ASCII

    $manifest = [ordered]@{
        created_utc = (Get-Date).ToUniversalTime().ToString("o")
        database_server_major = 18
        pg_dump_version = $versionText
        archive_format = "PostgreSQL custom format"
        archive_file = (Split-Path -Leaf $archivePath)
        archive_bytes = (Get-Item -LiteralPath $archivePath).Length
        sha256 = $fileHash
        restore_list_verified = $true
        credentials_in_manifest = $false
        restore_target = "A separate, empty PostgreSQL database only"
    }
    $manifest | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $manifestPath -Encoding UTF8

    Write-Host ""
    Write-Host "BACKUP CREATED AND ARCHIVE-READ CHECK PASSED"
    Write-Host "Archive:   $archivePath"
    Write-Host "SHA-256:   $hashPath"
    Write-Host "TOC list:  $tocPath"
    Write-Host "Manifest:  $manifestPath"
    Write-Host "Copy the archive and its hash to a second encrypted location. Do not commit them to Git."
}
catch {
    Write-Error $_
    if (Test-Path -LiteralPath $archivePath) {
        Write-Warning "A partial archive may exist at $archivePath. Do not treat it as a valid backup."
    }
    exit 1
}
finally {
    if ($null -ne $previousPgDatabase) {
        $env:PGDATABASE = $previousPgDatabase
    }
    else {
        Remove-Item Env:\PGDATABASE -ErrorAction SilentlyContinue
    }
    if ($null -ne $plainUrl) {
        $plainUrl = $null
    }
    if ($ptr -ne [IntPtr]::Zero) {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    }
    if ($null -ne $secureUrl) {
        $secureUrl.Dispose()
    }
}
