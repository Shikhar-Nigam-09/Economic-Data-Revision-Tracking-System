<#
.SYNOPSIS
    Automated Local Synchronization Script for Economic Data Monitoring System (Windows PowerShell).

.DESCRIPTION
    1. Checks if Git is available.
    2. Runs 'git pull --ff-only' to fetch the latest Parquet snapshots committed by GitHub Actions.
    3. Runs 'python -m econ_monitor.cli sync' to import new snapshots, detect revisions, compute
       health scores, detect anomalies, and dispatch notifications to Telegram.
    4. Logs output with UTC timestamps to logs/sync_local.log.

.EXAMPLE
    .\scripts\sync_local.ps1
    .\scripts\sync_local.ps1 -NoPull
    .\scripts\sync_local.ps1 -DryRunNotify
#>

[CmdletBinding()]
param (
    [switch]$NoPull,
    [switch]$NoNotify,
    [switch]$DryRunNotify,
    [string]$SnapshotsDir = "data/snapshots"
)

$ErrorActionPreference = "Stop"

# Ensure we run from the project root directory
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
$ProjectRoot = Split-Path -Parent $ScriptDir
Set-Location -Path $ProjectRoot

# Ensure logs directory exists
$LogDir = Join-Path $ProjectRoot "logs"
if (-not (Test-Path -Path $LogDir)) {
    New-Item -ItemType Directory -Path $LogDir | Out-Null
}
$LogFile = Join-Path $LogDir "sync_local.log"

function Write-SyncLog {
    param ([string]$Message)
    $Timestamp = (Get-Date).ToUniversalTime().ToString("yyyy-MM-dd HH:mm:ss UTC")
    $LogLine = "[$Timestamp] $Message"
    Write-Host $LogLine
    Add-Content -Path $LogFile -Value $LogLine
}

Write-SyncLog "=========================================================="
Write-SyncLog "Starting Local Synchronization Pipeline"
Write-SyncLog "Working Directory: $ProjectRoot"

# 1. Pull latest commits if requested (default: pull enabled)
if (-not $NoPull) {
    Write-SyncLog "Running 'git pull --ff-only' to pull remote Parquet snapshots..."
    try {
        $gitOutput = git pull --ff-only 2>&1
        Write-SyncLog "Git output: $gitOutput"
    } catch {
        Write-SyncLog "ERROR: Git pull failed: $_"
        exit 1
    }
} else {
    Write-SyncLog "Skipping git pull (-NoPull flag active)."
}

# 2. Build sync command arguments
$SyncArgs = @("-m", "econ_monitor.cli", "sync", "--dir", $SnapshotsDir)
if ($NoNotify) {
    $SyncArgs += "--no-notify"
}
if ($DryRunNotify) {
    $SyncArgs += "--dry-run-notify"
}

Write-SyncLog "Executing python $($SyncArgs -join ' ')..."

try {
    & python $SyncArgs 2>&1 | ForEach-Object {
        Write-SyncLog $_
    }
    if ($LASTEXITCODE -eq 0) {
        Write-SyncLog "Local Synchronization Pipeline completed successfully."
        exit 0
    } else {
        Write-SyncLog "ERROR: Pipeline exited with return code $LASTEXITCODE."
        exit $LASTEXITCODE
    }
} catch {
    Write-SyncLog "FATAL ERROR during sync execution: $_"
    exit 1
}
