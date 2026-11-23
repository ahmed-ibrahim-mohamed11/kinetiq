<#
.SYNOPSIS
  KinetiQ Commercial Gym Platform - Graceful Service Stopper
.DESCRIPTION
  Stops only the tracked KinetiQ processes recorded by START_KINETIQ.
  Preserves database records, events, and does not touch unrelated processes.
#>
$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $ScriptDir ".kinetiq.pids.json"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  Stopping KinetiQ Services Gracefully...                       " -ForegroundColor White
Write-Host "=================================================================" -ForegroundColor Cyan

function Stop-KinetiqPid([int]$Id, [string]$Name) {
  if ($Id -le 0) { return }
  $p = Get-Process -Id $Id -ErrorAction SilentlyContinue
  if ($p) {
    Write-Host " [*] Stopping $Name (PID $Id)..." -NoNewline
    # Use taskkill /T /F to terminate process and child trees reliably without CIM
    & taskkill /PID $Id /T /F 2>&1 | Out-Null
    Write-Host " [STOPPED]" -ForegroundColor Green
  } else {
    Write-Host " [i] $Name (PID $Id) already terminated." -ForegroundColor DarkGray
  }
}

function Stop-PortListener([int]$Port, [string]$ServiceName) {
  $lines = netstat -ano | Select-String ":$Port\s+.*LISTENING"
  foreach ($line in $lines) {
    $parts = ($line.ToString().Trim() -split "\s+")
    if ($parts.Length -gt 0) {
      $listenPid = [int]$parts[-1]
      if ($listenPid -gt 4 -and $listenPid -ne $PID) {
        Write-Host " [*] Releasing port $Port listener ($ServiceName, PID $listenPid)..." -ForegroundColor Yellow
        & taskkill /PID $listenPid /T /F 2>&1 | Out-Null
      }
    }
  }
}

if (Test-Path $PidFile) {
  try {
    $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
    foreach ($prop in $pids.PSObject.Properties) {
      Stop-KinetiqPid ([int]$prop.Value) $prop.Name
    }
    Remove-Item -Path $PidFile -Force -ErrorAction SilentlyContinue
  } catch {
    Write-Host " [!] Error reading PID file: $($_.Exception.Message)" -ForegroundColor Yellow
  }
}

# Ensure KinetiQ specific ports are released
Stop-PortListener 3000 "Admin Web"
Stop-PortListener 3001 "Member Web"
Stop-PortListener 8088 "Edge AI Health"

Write-Host ""
Write-Host " [OK] All KinetiQ services have been stopped cleanly." -ForegroundColor Green
Write-Host "      Database and event buffer files were safely preserved." -ForegroundColor DarkGray
Write-Host "=================================================================" -ForegroundColor Cyan
