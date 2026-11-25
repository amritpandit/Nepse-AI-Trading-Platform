#requires -Version 5.1
<#
.SYNOPSIS
Creates an independent, custom-format backup of the existing Render PostgreSQL database.

.DESCRIPTION
Uses the External Connection host from the Render dashboard. The password is
requested securely and is never written to the script, command line or output.
The script only runs pg_dump and pg_restore --list; it does not restore, migrate,
update or otherwise write to the remote database.

.EXAMPLE
.\scripts\backup_render_postgres.ps1 -HostName "your-db-host.render.com"

Requires PostgreSQL 18 client tools (pg_dump and pg_restore) on PATH.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidateNotNullOrEmpty()]
    [string]$HostName,

    [ValidateRange(1, 65535)]
    [int]$Port = 5432,

    [ValidateNotNullOrEmpty()]
    [string]$Database = "nepse_ai",

    [ValidateNotNullOrEmpty()]
    [string]$UserName = "nepse",

    [string]$OutputDirectory = (Join-Path $HOME "nepse-ai-backups")
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-NativeCommand {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "$Name was not found on PATH. Install PostgreSQL 18 command-line tools first."
    }
}

Assert-NativeCommand -Name "pg_dump"
Assert-NativeCommand -Name "pg_restore"

$clientVersionText = (& pg_dump --version)
if ($LASTEXITCODE -ne 0) {
    throw "Could not read the pg_dump version."
}
if ($clientVersionText -notmatch "PostgreSQL\)\s+(\d+)") {
    throw "Could not parse pg_dump version: $clientVersionText"
}
$clientMajor = [int]$Matches[1]
if ($clientMajor -lt 18) {
    throw "Use PostgreSQL 18 or newer client tools; found: $clientVersionText"
}

New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$dumpPath = Join-Path $OutputDirectory "nepse_ai_render_$timestamp.dump"
$tocPath = "$dumpPath.toc.txt"

$previousPassword = $env:PGPASSWORD
$previousSslMode = $env:PGSSLMODE
$credential = $null

try {
    $credential = Get-Credential -UserName $UserName -Message "Render PostgreSQL password for $Database"
    if ($null -eq $credential) {
        throw "No database credential was supplied."
    }

    $env:PGPASSWORD = $credential.GetNetworkCredential().Password
    $env:PGSSLMODE = "require"

    $dumpArgs = @(
        "--host=$HostName",
        "--port=$Port",
        "--username=$UserName",
        "--dbname=$Database",
        "--no-password",
        "--format=custom",
        "--no-owner",
        "--no-privileges",
        "--file=$dumpPath"
    )

    Write-Host "Creating database backup in: $dumpPath"
    & pg_dump @dumpArgs
    if ($LASTEXITCODE -ne 0) {
        throw "pg_dump failed with exit code $LASTEXITCODE. The backup is not verified."
    }

    Write-Host "Validating backup archive..."
    $tocOutput = & pg_restore --list $dumpPath
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore could not read the backup archive."
    }
    $tocOutput | Set-Content -Path $tocPath -Encoding UTF8

    $file = Get-Item -LiteralPath $dumpPath
    $hash = Get-FileHash -LiteralPath $dumpPath -Algorithm SHA256

    Write-Host ""
    Write-Host "Backup archive validated."
    Write-Host ("File:   {0}" -f $file.FullName)
    Write-Host ("Size:   {0:N0} bytes" -f $file.Length)
    Write-Host ("SHA256: {0}" -f $hash.Hash)
    Write-Host ("TOC:    {0}" -f $tocPath)
    Write-Host ""
    Write-Warning "Copy the dump to a second secure location and test-restore into a NEW, empty local PostgreSQL 18 database. Do not restore over Neon or the Render source."
}
finally {
    if ($null -ne $credential) {
        $credential = $null
    }
    if ($null -eq $previousPassword) {
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }
    else {
        $env:PGPASSWORD = $previousPassword
    }
    if ($null -eq $previousSslMode) {
        Remove-Item Env:PGSSLMODE -ErrorAction SilentlyContinue
    }
    else {
        $env:PGSSLMODE = $previousSslMode
    }
}
