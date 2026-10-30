<#
.SYNOPSIS
  First cloud deployment for AI Smart Gym: Supabase (migrations, secrets, Edge Functions, auth config)
  and GitHub (private repository + push). Run from the repository root in a regular terminal.

.DESCRIPTION
  Every step prints what it is about to do and the script stops at the first failure.
  Prerequisites (one-time, interactive):
    npx supabase login        # browser flow; the project is already linked (supabase/.temp/project-ref)
    gh auth login --web       # GitHub device flow

  The script never runs supabase/seed.sql (demo tenant + dev device secret) against the cloud project.
  QR_TOKEN_SECRET is generated locally with a CSPRNG and is never printed or stored on disk.

.PARAMETER SkipSupabase
  Skip the Supabase section (migrations, secrets, functions, config).
.PARAMETER SkipGitHub
  Skip the GitHub section (repo creation + push).
.PARAMETER RepoName
  GitHub repository name to create under the logged-in account. Default: ai-smart-gym (private).
#>
param(
  [switch]$SkipSupabase,
  [switch]$SkipGitHub,
  [string]$RepoName = 'ai-smart-gym'
)

$ErrorActionPreference = 'Stop'
function Step([string]$text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail([string]$text) { Write-Host "FAILED: $text" -ForegroundColor Red; exit 1 }

if (-not (Test-Path 'supabase/config.toml')) { Fail 'Run this from the repository root.' }

if (-not $SkipSupabase) {
  if (-not (Test-Path 'supabase/.temp/project-ref')) { Fail 'Project not linked. Run: npx supabase link --project-ref vpwudyxfsbyrbpwqdmew' }
  $ref = (Get-Content 'supabase/.temp/project-ref').Trim()
  Write-Host "Linked Supabase project: $ref"

  Step 'Supabase: show pending migrations (dry run)'
  npx supabase db push --linked --dry-run --yes
  if ($LASTEXITCODE -ne 0) { Fail 'dry run' }

  Step 'Supabase: apply migrations (no seed)'
  npx supabase db push --linked --yes
  if ($LASTEXITCODE -ne 0) { Fail 'db push' }

  Step 'Supabase: set Edge Function secrets (QR_TOKEN_SECRET generated locally, not printed)'
  $bytes = New-Object byte[] 32
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
  npx supabase secrets set "QR_TOKEN_SECRET=$([Convert]::ToBase64String($bytes))" 'INGEST_CLOCK_SKEW_SECONDS=300'
  if ($LASTEXITCODE -ne 0) { Fail 'secrets set' }

  Step 'Supabase: deploy Edge Functions (server-side bundling, no Docker)'
  npx supabase functions deploy ingest-events --use-api --no-verify-jwt
  if ($LASTEXITCODE -ne 0) { Fail 'deploy ingest-events' }
  npx supabase functions deploy device-sync --use-api --no-verify-jwt
  if ($LASTEXITCODE -ne 0) { Fail 'deploy device-sync' }
  npx supabase functions deploy qr-token --use-api
  if ($LASTEXITCODE -ne 0) { Fail 'deploy qr-token' }

  Step 'Supabase: review config differences (auth signups off, redirect URLs, storage bucket)'
  npx supabase config diff
  Step 'Supabase: push declared config.toml settings'
  npx supabase config push --yes
  if ($LASTEXITCODE -ne 0) { Fail 'config push' }

  Step 'Supabase: verify'
  npx supabase migration list --linked
  npx supabase functions list --project-ref $ref
  npx supabase secrets list
}

if (-not $SkipGitHub) {
  Step 'GitHub: make gh the git credential helper for github.com'
  gh auth setup-git
  if ($LASTEXITCODE -ne 0) { Fail 'gh auth setup-git (run: gh auth login --web)' }

  $dirty = git status --porcelain
  if ($dirty) { Fail 'Working tree is not clean; commit or stash first.' }

  Step "GitHub: create private repository '$RepoName' and push main"
  gh repo create $RepoName --private --source . --remote origin --push `
    --description 'AI Smart Gym: consent-based CCTV workout tracking (edge AI + Supabase + Next.js)'
  if ($LASTEXITCODE -ne 0) { Fail 'gh repo create (if it already exists: git remote add origin <url>; git push -u origin main)' }

  Step 'GitHub: first CI run'
  gh run list --limit 3
  Write-Host 'Watch with: gh run watch' -ForegroundColor Yellow
}

Write-Host "`nDone. Next: connect Vercel to the GitHub repository (docs/deployment/vercel.md) and run the live RLS suite:" -ForegroundColor Green
Write-Host '  $env:RUN_RLS_TESTS=1; npm run test:rls -w tests/security   # reads SUPABASE_* from the root .env'
