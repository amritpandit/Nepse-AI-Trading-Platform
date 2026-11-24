[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$ArchivePath
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

function ConvertTo-PgPassField {
    param([Parameter(Mandatory = $true)][string]$Value)
    return $Value.Replace("\", "\\").Replace(":", "\:")
}

$pgRestore = Get-PostgresTool -Name "pg_restore"
$psql = Get-PostgresTool -Name "psql"

$versionText = (& $pgRestore --version 2>&1 | Out-String).Trim()
if ($LASTEXITCODE -ne 0 -or $versionText -notmatch "PostgreSQL\) (?<major>\d+)") {
    throw "Could not determine pg_restore version."
}
if ([int]$Matches["major"] -lt 18) {
    throw "Use PostgreSQL 18 or newer client tools; detected: $versionText"
}

$ArchivePath = (Resolve-Path -LiteralPath $ArchivePath).Path
$hashPath = "$ArchivePath.sha256"
if (-not (Test-Path -LiteralPath $hashPath)) {
    throw "The SHA-256 sidecar file is missing: $hashPath"
}
$hashLine = (Get-Content -LiteralPath $hashPath -Raw).Trim()
$expectedHash = ($hashLine -split "\s+")[0].ToLowerInvariant()
$actualHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256).Hash.ToLowerInvariant()
if ($expectedHash -notmatch "^[0-9a-f]{64}$" -or $expectedHash -ne $actualHash) {
    throw "Archive SHA-256 verification failed. Do not restore this file."
}

$null = & $pgRestore --list $ArchivePath
if ($LASTEXITCODE -ne 0) {
    throw "pg_restore could not read the archive directory."
}

$secureUrl = $null
$databaseUrl = $null
$databasePassword = $null
$pgPassDirectory = $null
$previousPgPassFile = $env:PGPASSFILE
$previousPgSslMode = $env:PGSSLMODE

try {
    $secureUrl = Read-Host "Paste the SEPARATE empty target database URL (input is hidden)" -AsSecureString
    $urlPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureUrl)
    try {
        $databaseUrl = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($urlPointer)
    }
    finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($urlPointer)
    }

    $uri = [Uri]$databaseUrl
    if ($uri.Scheme -notin @("postgres", "postgresql") -or [string]::IsNullOrWhiteSpace($uri.Host)) {
        throw "The supplied value must be a PostgreSQL connection URL."
    }
    if ($uri.Host -match "(?i)(^|\.)render\.com$") {
        throw "This helper refuses Render hosts. Restore to a separate local/test PostgreSQL database only."
    }

    $userInfo = $uri.UserInfo
    $separatorIndex = $userInfo.IndexOf(":")
    if ($separatorIndex -le 0) {
        throw "The PostgreSQL URL must include a username and password."
    }
    $databaseUser = [Uri]::UnescapeDataString($userInfo.Substring(0, $separatorIndex))
    $databasePassword = [Uri]::UnescapeDataString($userInfo.Substring($separatorIndex + 1))
    $databaseName = [Uri]::UnescapeDataString($uri.AbsolutePath.TrimStart("/"))
    if ([string]::IsNullOrWhiteSpace($databaseName)) {
        throw "The PostgreSQL URL does not contain a database name."
    }

    $port = if ($uri.Port -gt 0) { $uri.Port } else { 5432 }
    $sslMode = "prefer"
    foreach ($part in $uri.Query.TrimStart("?").Split("&")) {
        if ([string]::IsNullOrWhiteSpace($part)) { continue }
        $querySeparator = $part.IndexOf("=")
        if ($querySeparator -gt 0) {
            $queryKey = [Uri]::UnescapeDataString($part.Substring(0, $querySeparator))
            if ($queryKey -eq "sslmode") {
                $sslMode = [Uri]::UnescapeDataString($part.Substring($querySeparator + 1))
            }
        }
    }
    if ($sslMode -notin @("disable", "allow", "prefer", "require", "verify-ca", "verify-full")) {
        throw "Unsupported PostgreSQL sslmode in target URL."
    }

    $pgPassDirectory = Join-Path $env:TEMP ("nepse-ai-restore-pgpass-" + [Guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $pgPassDirectory | Out-Null
    $account = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
    & icacls.exe $pgPassDirectory /inheritance:r /grant:r "${account}:(OI)(CI)(F)" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Could not restrict access to the temporary PostgreSQL credential directory."
    }

    $pgPassFile = Join-Path $pgPassDirectory "pgpass.conf"
    $pgPassLine = @(
        (ConvertTo-PgPassField $uri.Host),
        [string]$port,
        (ConvertTo-PgPassField $databaseName),
        (ConvertTo-PgPassField $databaseUser),
        (ConvertTo-PgPassField $databasePassword)
    ) -join ":"
    Set-Content -LiteralPath $pgPassFile -Value $pgPassLine -Encoding ASCII -NoNewline
    $env:PGPASSFILE = $pgPassFile
    $env:PGSSLMODE = $sslMode

    $applicationObjectCountSql = @"
SELECT
    (SELECT COUNT(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
     WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
       AND c.relkind IN ('r', 'p', 'v', 'm', 'S', 'f'))
  + (SELECT COUNT(*) FROM pg_proc p JOIN pg_namespace n ON n.oid = p.pronamespace
     WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast'))
  + (SELECT COUNT(*) FROM pg_namespace
     WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast', 'public'));
"@
    $applicationObjectCountOutput = & $psql --no-password --quiet --no-align --tuples-only --set ON_ERROR_STOP=1 --host $uri.Host --port $port --username $databaseUser --dbname $databaseName --command $applicationObjectCountSql 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Could not verify target database emptiness. Restore cancelled."
    }
    $applicationObjectCountText = ($applicationObjectCountOutput | Out-String).Trim()
    $applicationObjectCount = 0
    if (-not [int]::TryParse($applicationObjectCountText, [ref]$applicationObjectCount)) {
        throw "Could not parse target application-object count. Restore cancelled."
    }
    if ($applicationObjectCount -ne 0) {
        throw "Target contains $applicationObjectCount application objects. Restore cancelled; use a newly created empty database."
    }

    Write-Host "Archive SHA-256 verified: $actualHash"
    Write-Host "Target host: $($uri.Host)"
    Write-Host "Target database: $databaseName"
    Write-Host "Target application objects: 0"
    Write-Host "The restore will write to this non-Render target database."

    $confirmation = Read-Host 'Type exactly RESTORE TO EMPTY DATABASE to continue'
    if ($confirmation -cne "RESTORE TO EMPTY DATABASE") {
        throw "Confirmation did not match. Restore cancelled."
    }

    & $pgRestore --no-password --exit-on-error --verbose --no-owner --no-privileges --host $uri.Host --port $port --username $databaseUser --dbname $databaseName $ArchivePath
    if ($LASTEXITCODE -ne 0) {
        throw "pg_restore failed with exit code $LASTEXITCODE."
    }

    Write-Host "RESTORE COMPLETED. Keep this database isolated and verify its row counts before considering any cutover."
}
catch {
    Write-Error $_
    exit 1
}
finally {
    if ($null -ne $previousPgPassFile) {
        $env:PGPASSFILE = $previousPgPassFile
    }
    else {
        Remove-Item Env:\PGPASSFILE -ErrorAction SilentlyContinue
    }
    if ($null -ne $previousPgSslMode) {
        $env:PGSSLMODE = $previousPgSslMode
    }
    else {
        Remove-Item Env:\PGSSLMODE -ErrorAction SilentlyContinue
    }
    if ($null -ne $pgPassDirectory -and (Test-Path -LiteralPath $pgPassDirectory)) {
        Remove-Item -LiteralPath $pgPassDirectory -Recurse -Force
    }
    $databasePassword = $null
    $databaseUrl = $null
    if ($null -ne $secureUrl) {
        $secureUrl.Dispose()
    }
}
