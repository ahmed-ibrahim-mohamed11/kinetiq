<#
.SYNOPSIS
  KinetiQ Commercial Gym Platform - Comprehensive System Health Inspector
.DESCRIPTION
  Queries live health endpoints, process memory consumption, Edge AI vision pipelines,
  camera FPS, and event queue synchronization state.
#>
$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PidFile = Join-Path $ScriptDir ".kinetiq.pids.json"

Clear-Host
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "  K I N E T I Q  --  System Health & Telemetry Inspector        " -ForegroundColor White
Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host " Timestamp: $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') EET" -ForegroundColor DarkGray
Write-Host ""

function Check-Endpoint([string]$Url, [int]$TimeoutMs = 1200) {
  try {
    $req = [System.Net.WebRequest]::Create($Url)
    $req.Timeout = $TimeoutMs
    $resp = $req.GetResponse()
    $stream = $resp.GetResponseStream()
    $reader = New-Object System.IO.StreamReader($stream)
    $content = $reader.ReadToEnd()
    $reader.Close()
    $stream.Close()
    $resp.Close()
    return @{ Ok = $true; Json = ($content | ConvertFrom-Json) }
  } catch {
    return @{ Ok = $false; Error = $_.Exception.Message }
  }
}

# 1. Admin Web
$adminHealth = Check-Endpoint "http://127.0.0.1:3000/api/health"
if ($adminHealth.Ok) {
  Write-Host "  [+] Admin Web         : " -NoNewline; Write-Host "ONLINE" -ForegroundColor Green -NoNewline; Write-Host " (Port 3000, http://localhost:3000)"
} else {
  Write-Host "  [-] Admin Web         : " -NoNewline; Write-Host "OFFLINE" -ForegroundColor Red -NoNewline; Write-Host " (Port 3000)"
}

# 2. Member Web
$memberHealth = Check-Endpoint "http://127.0.0.1:3001/api/health"
if ($memberHealth.Ok) {
  Write-Host "  [+] Member Web        : " -NoNewline; Write-Host "ONLINE" -ForegroundColor Green -NoNewline; Write-Host " (Port 3001, http://localhost:3001)"
} else {
  Write-Host "  [-] Member Web        : " -NoNewline; Write-Host "OFFLINE" -ForegroundColor Red -NoNewline; Write-Host " (Port 3001)"
}

# 3. Edge AI Daemon
$edgeHealth = Check-Endpoint "http://127.0.0.1:8088/healthz"
if ($edgeHealth.Ok) {
  $data = $edgeHealth.Json
  Write-Host "  [+] Edge AI Vision    : " -NoNewline; Write-Host "ONLINE" -ForegroundColor Green -NoNewline; Write-Host " (Port 8088, Uptime: $($data.uptime_seconds)s)"
  
  # Inference details
  $provider = if ($data.active_provider) { $data.active_provider } else { "CPUExecutionProvider" }
  $detector = if ($data.stages.detector) { $data.stages.detector } else { "YOLOX-Nano" }
  $pose = if ($data.stages.pose) { $data.stages.pose } else { "RTMPose-M" }
  Write-Host "      - Inference Engine: $detector + $pose [$provider]" -ForegroundColor DarkCyan
  
  # Camera details
  if ($data.cameras) {
    foreach ($camProp in $data.cameras.PSObject.Properties) {
      $c = $camProp.Value
      Write-Host "      - Camera '$($c.name)': $($c.status.ToUpper()) ($($c.fps) FPS capture / $($c.inference_fps) FPS inference)" -ForegroundColor DarkCyan
    }
  }

  # Event Sync details
  $qDepth = $data.queue_depth
  $lastErr = $data.sync_last_error
  if ($data.backend_connected) {
    Write-Host "      - Cloud Event Sync: " -NoNewline; Write-Host "CONNECTED" -ForegroundColor Green -NoNewline; Write-Host " (Queue: $qDepth events buffered)"
  } else {
    Write-Host "      - Cloud Event Sync: " -NoNewline; Write-Host "OFFLINE BUFFER" -ForegroundColor Yellow -NoNewline; Write-Host " (Queue: $qDepth events buffered locally in SQLite)"
    if ($lastErr) {
      Write-Host "        Reason: $lastErr" -ForegroundColor DarkGray
    }
  }

  # Workout Reps
  Write-Host "      - Reps Admitted   : $($data.reps_admitted) (Duplicates suppressed: $($data.reps_suppressed_duplicates))" -ForegroundColor DarkCyan
} else {
  Write-Host "  [-] Edge AI Vision    : " -NoNewline; Write-Host "OFFLINE" -ForegroundColor Red -NoNewline; Write-Host " (Port 8088 not listening)"
}

# 4. Process Memory Consumption
Write-Host ""
Write-Host "  Process Resource Metrics:" -ForegroundColor White
$totalWs = 0
if (Test-Path $PidFile) {
  try {
    $pids = Get-Content $PidFile -Raw | ConvertFrom-Json
    foreach ($prop in $pids.PSObject.Properties) {
      $p = Get-Process -Id $prop.Value -ErrorAction SilentlyContinue
      if ($p) {
        $mb = [math]::Round($p.WorkingSet64 / 1MB, 1)
        $totalWs += $mb
        Write-Host "  - $($prop.Name) [PID $($p.Id)]: $mb MB RAM" -ForegroundColor Gray
      }
    }
    Write-Host "  Total KinetiQ Memory Footprint: $totalWs MB" -ForegroundColor Yellow
  } catch {}
} else {
  Write-Host "  (Run START_KINETIQ.bat to launch and track processes)" -ForegroundColor DarkGray
}

Write-Host "=================================================================" -ForegroundColor Cyan

