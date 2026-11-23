<#
.SYNOPSIS
  KinetiQ Commercial Gym Platform - One-Click Production Launcher
.DESCRIPTION
  Safely starts Admin Web, Member Web, and Edge AI services in high-performance
  production mode. Tracks process IDs to ensure clean shutdown and avoids duplicate instances.
.PARAMETER Dev
  Start in Next.js development mode with Turbopack instead of production mode.
.PARAMETER Rebuild
  Force an upfront production rebuild of both Next.js applications before starting.
.PARAMETER NoBrowser
  Do not automatically launch the browser after services become ready.
.PARAMETER NoEdgeAI
  Skip starting the Python Edge AI camera daemon.
#>
[CmdletBinding()]
param(
  [switch]$Dev,
  [switch]$Rebuild,
  [switch]$NoBrowser,
  [switch]$NoEdgeAI
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoDir = Join-Path $ScriptDir "omar"
$PidFile = Join-Path $ScriptDir ".kinetiq.pids.json"
$LogDir = Join-Path $ScriptDir ".kinetiq.logs"

if (-not (Test-Path $LogDir)) {
  New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
}

function Write-KinetiqBanner {
  Clear-Host
  Write-Host "=================================================================" -ForegroundColor Cyan
  Write-Host "  K I N E T I Q  --  Smart Commercial Gym Management Platform  " -ForegroundColor White
  Write-Host "=================================================================" -ForegroundColor Cyan
  Write-Host " Root Directory : $ScriptDir" -ForegroundColor DarkGray
  Write-Host " Operating Mode : $(if ($Dev) { 'Development (Turbopack)' } else { 'Production (Optimized Build)' })" -ForegroundColor Yellow
  Write-Host ""
}

function Test-PortOpen([string]$HostName, [int]$Port) {
  try {
    $client = New-Object System.Net.Sockets.TcpClient
    $iar = $client.BeginConnect($HostName, $Port, $null, $null)
    $success = $iar.AsyncWaitHandle.WaitOne(400, $false)
    if ($success -and $client.Connected) {
      $client.EndConnect($iar)
      $client.Close()
      return $true
    }
    $client.Close()
    return $false
  } catch {
    return $false
  }
}

function Wait-HttpReady([string]$Url, [int]$TimeoutSeconds = 25, [string]$ServiceName = "Service") {
  Write-Host " [*] Waiting for $ServiceName to be ready on $Url ..." -NoNewline
  $start = [DateTime]::UtcNow
  while (([DateTime]::UtcNow - $start).TotalSeconds -lt $TimeoutSeconds) {
    try {
      $req = [System.Net.WebRequest]::Create($Url)
      $req.Timeout = 1500
      $resp = $req.GetResponse()
      if ($resp.StatusCode -eq 200) {
        $resp.Close()
        Write-Host " [ONLINE]" -ForegroundColor Green
        return $true
      }
      $resp.Close()
    } catch {
      Start-Sleep -Milliseconds 500
    }
  }
  Write-Host " [TIMEOUT]" -ForegroundColor Yellow
  return $false
}

Write-KinetiqBanner

# 1. Check existing PID tracking
if (Test-Path $PidFile) {
  try {
    $existing = Get-Content $PidFile -Raw | ConvertFrom-Json
    $runningAny = $false
    foreach ($prop in $existing.PSObject.Properties) {
      $p = Get-Process -Id $prop.Value -ErrorAction SilentlyContinue
      if ($p) { $runningAny = $true }
    }
    if ($runningAny) {
      Write-Host " [!] KinetiQ is already running (PID file exists). Run STOP_KINETIQ.bat first to restart." -ForegroundColor Yellow
      exit 0
    }
  } catch {}
}

# 2. Check Port Availability
$portsToCheck = @(
  @{ Name = "Admin Web"; Port = 3000 },
  @{ Name = "Member Web"; Port = 3001 },
  @{ Name = "Edge AI"; Port = 8088 }
)

foreach ($pt in $portsToCheck) {
  if (Test-PortOpen "127.0.0.1" $pt.Port) {
    Write-Host " [!] Port $($pt.Port) ($($pt.Name)) is already in use by another process." -ForegroundColor Yellow
  }
}

# 3. Production Builds (if not dev and rebuild requested or build missing)
$adminBuildDir = Join-Path $RepoDir "apps\admin-web\.next"
$memberBuildDir = Join-Path $RepoDir "apps\member-web\.next"

if (-not $Dev) {
  $needsAdminBuild = $Rebuild -or (-not (Test-Path $adminBuildDir))
  $needsMemberBuild = $Rebuild -or (-not (Test-Path $memberBuildDir))

  if ($needsAdminBuild) {
    Write-Host " [*] Compiling optimized production build for Admin Web..." -ForegroundColor Cyan
    & npm.cmd --prefix "$RepoDir/apps/admin-web" run build
    if ($LASTEXITCODE -ne 0) {
      Write-Host " [X] Admin Web build failed. See logs above." -ForegroundColor Red
      exit 1
    }
  }

  if ($needsMemberBuild) {
    Write-Host " [*] Compiling optimized production build for Member Web..." -ForegroundColor Cyan
    & npm.cmd --prefix "$RepoDir/apps/member-web" run build
    if ($LASTEXITCODE -ne 0) {
      Write-Host " [X] Member Web build failed. See logs above." -ForegroundColor Red
      exit 1
    }
  }
}

$trackedPids = @{}
$nextBin = Join-Path $RepoDir "node_modules\next\dist\bin\next"
$adminAppDir = Join-Path $RepoDir "apps\admin-web"
$memberAppDir = Join-Path $RepoDir "apps\member-web"

# 4. Start Admin Web
$adminLog = Join-Path $LogDir "admin-web.log"
$adminArgs = if ($Dev) {
  @("$nextBin", "dev", "--turbo", "$adminAppDir", "-H", "127.0.0.1", "-p", "3000")
} else {
  @("$nextBin", "start", "$adminAppDir", "-H", "127.0.0.1", "-p", "3000")
}
Write-Host " [*] Starting Admin Web on port 3000..." -ForegroundColor Cyan
$adminProc = Start-Process -FilePath "node.exe" -ArgumentList $adminArgs -WorkingDirectory $adminAppDir `
  -RedirectStandardOutput $adminLog -RedirectStandardError (Join-Path $LogDir "admin-web.err.log") `
  -WindowStyle Hidden -PassThru
$trackedPids["admin_web"] = $adminProc.Id

# 5. Start Member Web
$memberLog = Join-Path $LogDir "member-web.log"
$memberArgs = if ($Dev) {
  @("$nextBin", "dev", "--turbo", "$memberAppDir", "-H", "127.0.0.1", "-p", "3001")
} else {
  @("$nextBin", "start", "$memberAppDir", "-H", "127.0.0.1", "-p", "3001")
}
Write-Host " [*] Starting Member Web on port 3001..." -ForegroundColor Cyan
$memberProc = Start-Process -FilePath "node.exe" -ArgumentList $memberArgs -WorkingDirectory $memberAppDir `
  -RedirectStandardOutput $memberLog -RedirectStandardError (Join-Path $LogDir "member-web.err.log") `
  -WindowStyle Hidden -PassThru
$trackedPids["member_web"] = $memberProc.Id

# 6. Start Edge AI Daemon (if enabled)
if (-not $NoEdgeAI) {
  $edgePython = Join-Path $RepoDir "services\edge-ai\.venv\Scripts\python.exe"
  if (Test-Path $edgePython) {
    $edgeLog = Join-Path $LogDir "edge-ai.log"
    Write-Host " [*] Starting Edge AI Vision Service on port 8088..." -ForegroundColor Cyan
    $edgeProc = Start-Process -FilePath $edgePython -ArgumentList "-m edge_ai run" -WorkingDirectory (Join-Path $RepoDir "services/edge-ai") `
      -RedirectStandardOutput $edgeLog -RedirectStandardError (Join-Path $LogDir "edge-ai.err.log") `
      -WindowStyle Hidden -PassThru
    $trackedPids["edge_ai"] = $edgeProc.Id
  } else {
    Write-Host " [!] Edge AI virtual environment not found at $edgePython. Skipping daemon." -ForegroundColor Yellow
  }
}

# Save PID file
$trackedPids | ConvertTo-Json | Set-Content -Path $PidFile -Encoding UTF8

# 7. Perform Readiness Checks
Start-Sleep -Milliseconds 400
Wait-HttpReady "http://127.0.0.1:3000/api/health" 25 "Admin Web"
Wait-HttpReady "http://127.0.0.1:3001/api/health" 25 "Member Web"
if (-not $NoEdgeAI) {
  Wait-HttpReady "http://127.0.0.1:8088/healthz" 15 "Edge AI"
}

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host "  KinetiQ Services are Live & Operational!                      " -ForegroundColor White
Write-Host "=================================================================" -ForegroundColor Green
Write-Host "  Admin Web      : http://localhost:3000" -ForegroundColor Cyan
Write-Host "  Member Web     : http://localhost:3001" -ForegroundColor Cyan
Write-Host "  AI Diagnostics : http://localhost:3000/diagnostics" -ForegroundColor Cyan
Write-Host "  Edge AI Health : http://localhost:8088/healthz" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Green
Write-Host "  To view live status : ./STATUS_KINETIQ.bat" -ForegroundColor White
Write-Host "  To stop services    : ./STOP_KINETIQ.bat" -ForegroundColor White
Write-Host ""

# 8. Open default browser to Admin Web
if (-not $NoBrowser) {
  Start-Process "http://localhost:3000"
}
