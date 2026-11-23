# Benchmark full stack startup including Edge AI
$ErrorActionPreference = "SilentlyContinue"

Write-Host ">>> Running START_KINETIQ.ps1 in Full Production Mode (with Edge AI)..." -ForegroundColor Cyan
$swStart = [System.Diagnostics.Stopwatch]::StartNew()
& "./START_KINETIQ.ps1" -NoBrowser
$swStart.Stop()
Write-Host ">>> Total Full-Stack Startup Duration: $($swStart.ElapsedMilliseconds) ms" -ForegroundColor Green

Write-Host ""
Write-Host ">>> Testing Route Latencies..." -ForegroundColor Cyan

function Measure-Route([string]$Url, [string]$Label) {
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $resp = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
    $sw.Stop()
    Write-Host ("  [+] $Label ($Url): Status $($resp.StatusCode) in $($sw.ElapsedMilliseconds) ms") -ForegroundColor Green
    return $sw.ElapsedMilliseconds
  } catch {
    $sw.Stop()
    Write-Host ("  [-] $Label ($Url): Failed in $($sw.ElapsedMilliseconds) ms - $($_.Exception.Message)") -ForegroundColor Yellow
    return $sw.ElapsedMilliseconds
  }
}

Measure-Route "http://127.0.0.1:3000/api/health" "Admin Health"
Measure-Route "http://127.0.0.1:3000/login" "Admin Login"
Measure-Route "http://127.0.0.1:3001/api/health" "Member Health"
Measure-Route "http://127.0.0.1:3001/login" "Member Login"
Measure-Route "http://127.0.0.1:8088/healthz" "Edge AI Healthz"

Write-Host ""
Write-Host ">>> Querying System Status..." -ForegroundColor Cyan
& "./STATUS_KINETIQ.ps1"

Write-Host ""
Write-Host ">>> Stopping KinetiQ..." -ForegroundColor Cyan
& "./STOP_KINETIQ.ps1"
