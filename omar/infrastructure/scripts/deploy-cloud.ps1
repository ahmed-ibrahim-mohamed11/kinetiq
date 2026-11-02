<#
.SYNOPSIS
  Repeatable cloud deployment for AI Smart Gym: Supabase (migrations, function secrets, Edge Functions,
  Vault secrets for the pg_cron notification dispatcher, auth/storage config) and GitHub (push main).
  Run from the repository root in a regular terminal (Windows PowerShell 5.1 or PowerShell 7).

.DESCRIPTION
  Every step prints what it is about to do and the script stops at the first failure. It is safe to re-run:
  migrations are applied once, secrets are overwritten with the same values, Vault entries are updated in place.
  Prerequisites (one-time, interactive):
    npx supabase login ; npx supabase link --project-ref vpwudyxfsbyrbpwqdmew
    gh auth login --web  (and `gh auth refresh -s workflow` so .github/workflows can be pushed)
  Secrets are read from the gitignored root .env (SUPABASE_URL plus, optionally, QR_TOKEN_SECRET,
  NOTIFICATION_DISPATCH_SECRET, VAPID_PUBLIC_KEY, VAPID_PRIVATE_KEY, VAPID_SUBJECT, MEMBER_APP_URL,
  RESEND_API_KEY, EMAIL_FROM). Missing random secrets are generated with a CSPRNG and appended to .env so
  the next run reuses them. Nothing secret is printed.
  The script never runs supabase/seed.sql against the cloud project.

.PARAMETER SkipSupabase   Skip the Supabase section.
.PARAMETER SkipGitHub     Skip the GitHub section.
.PARAMETER RepoName       Repository name to create if no `origin` remote exists. Default: ai-smart-gym (private).
#>
param(
  [switch]$SkipSupabase,
  [switch]$SkipGitHub,
  [string]$RepoName = 'ai-smart-gym'
)

$ErrorActionPreference = 'Stop'
function Step([string]$text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail([string]$text) { Write-Host "FAILED: $text" -ForegroundColor Red; exit 1 }
function New-RandomSecret([int]$bytes = 32) {
  $buf = New-Object byte[] $bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
  return [Convert]::ToBase64String($buf).TrimEnd('=').Replace('+', '-').Replace('/', '_')
}
function Read-DotEnv([string]$path) {
  $map = @{}
  if (Test-Path $path) {
    foreach ($line in Get-Content $path) {
      if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $map[$matches[1]] = $matches[2].Trim() }
    }
  }
  return $map
}
function Ensure-EnvValue([hashtable]$map, [string]$key, [scriptblock]$generate, [string]$comment) {
  if ($map.ContainsKey($key) -and $map[$key]) { return }
  $value = & $generate
  $map[$key] = $value
  Add-Content -Path '.env' -Value "# $comment`n$key=$value" -Encoding UTF8
  Write-Host "  generated $key and appended it to .env"
}

if (-not (Test-Path 'supabase/config.toml')) { Fail 'Run this from the repository root.' }
$dotenv = Read-DotEnv '.env'

if (-not $SkipSupabase) {
  if (-not (Test-Path 'supabase/.temp/project-ref')) { Fail 'Project not linked. Run: npx supabase link --project-ref vpwudyxfsbyrbpwqdmew' }
  $ref = (Get-Content 'supabase/.temp/project-ref').Trim()
  $functionsUrl = if ($dotenv['SUPABASE_URL']) { "$($dotenv['SUPABASE_URL'])/functions/v1" } else { "https://$ref.supabase.co/functions/v1" }
  $memberAppUrl = if ($dotenv['MEMBER_APP_URL']) { $dotenv['MEMBER_APP_URL'] } else { 'https://smartgym-member.vercel.app' }
  Write-Host "Linked Supabase project: $ref"

  Step 'Supabase: pending migrations (dry run)'
  npx supabase db push --linked --dry-run --yes
  if ($LASTEXITCODE -ne 0) { Fail 'dry run' }

  Step 'Supabase: apply migrations (no seed)'
  npx supabase db push --linked --yes
  if ($LASTEXITCODE -ne 0) { Fail 'db push' }

  Step 'Supabase: ensure local secrets exist (generated once, kept in .env)'
  Ensure-EnvValue $dotenv 'QR_TOKEN_SECRET' { New-RandomSecret 32 } 'HMAC key for QR check-in tokens (qr-token function)'
  Ensure-EnvValue $dotenv 'NOTIFICATION_DISPATCH_SECRET' { New-RandomSecret 32 } 'Shared secret between pg_cron (Vault smartgym_dispatch_secret) and the notification-dispatch function'
  if (-not ($dotenv['VAPID_PUBLIC_KEY'] -and $dotenv['VAPID_PRIVATE_KEY'])) {
    $vapid = npx -y web-push generate-vapid-keys --json | ConvertFrom-Json
    $dotenv['VAPID_PUBLIC_KEY'] = $vapid.publicKey
    $dotenv['VAPID_PRIVATE_KEY'] = $vapid.privateKey
    Add-Content -Path '.env' -Value "# Web Push VAPID key pair (public half is safe to publish)`nVAPID_PUBLIC_KEY=$($vapid.publicKey)`nVAPID_PRIVATE_KEY=$($vapid.privateKey)`nNEXT_PUBLIC_VAPID_PUBLIC_KEY=$($vapid.publicKey)" -Encoding UTF8
    Write-Host '  generated VAPID keys and appended them to .env'
  }
  if (-not $dotenv['VAPID_SUBJECT']) { $dotenv['VAPID_SUBJECT'] = 'mailto:ops@example.com' }

  Step 'Supabase: set Edge Function secrets (via a temporary env file, never printed)'
  $tmpEnv = Join-Path $env:TEMP ("smartgym-secrets-" + [guid]::NewGuid().ToString('n') + ".env")
  $lines = @(
    "QR_TOKEN_SECRET=$($dotenv['QR_TOKEN_SECRET'])",
    'INGEST_CLOCK_SKEW_SECONDS=300',
    "NOTIFICATION_DISPATCH_SECRET=$($dotenv['NOTIFICATION_DISPATCH_SECRET'])",
    "VAPID_PUBLIC_KEY=$($dotenv['VAPID_PUBLIC_KEY'])",
    "VAPID_PRIVATE_KEY=$($dotenv['VAPID_PRIVATE_KEY'])",
    "VAPID_SUBJECT=$($dotenv['VAPID_SUBJECT'])",
    "MEMBER_APP_URL=$memberAppUrl"
  )
  if ($dotenv['RESEND_API_KEY'] -and $dotenv['EMAIL_FROM']) {
    $lines += "RESEND_API_KEY=$($dotenv['RESEND_API_KEY'])"
    $lines += "EMAIL_FROM=$($dotenv['EMAIL_FROM'])"
  }
  try {
    [System.IO.File]::WriteAllText($tmpEnv, ($lines -join "`n") + "`n", (New-Object System.Text.UTF8Encoding $false))
    npx supabase secrets set --env-file $tmpEnv
    if ($LASTEXITCODE -ne 0) { Fail 'secrets set' }
  } finally {
    Remove-Item $tmpEnv -Force -ErrorAction SilentlyContinue
  }

  Step 'Supabase: Vault secrets read by the pg_cron notification dispatcher'
  $tmpSql = Join-Path $env:TEMP ("smartgym-vault-" + [guid]::NewGuid().ToString('n') + ".sql")
  $sql = @"
do `$vault`$
declare v_id uuid;
begin
  if to_regnamespace('vault') is null then
    raise notice 'Vault is not available on this database; skipping';
    return;
  end if;
  select id into v_id from vault.secrets where name = 'smartgym_functions_url';
  if v_id is null then perform vault.create_secret('$functionsUrl', 'smartgym_functions_url', 'Base URL of this project''s Edge Functions, read by pg_cron jobs');
  else perform vault.update_secret(v_id, '$functionsUrl'); end if;
  select id into v_id from vault.secrets where name = 'smartgym_dispatch_secret';
  if v_id is null then perform vault.create_secret('$($dotenv['NOTIFICATION_DISPATCH_SECRET'])', 'smartgym_dispatch_secret', 'x-dispatch-secret header expected by the notification-dispatch function');
  else perform vault.update_secret(v_id, '$($dotenv['NOTIFICATION_DISPATCH_SECRET'])'); end if;
end
`$vault`$;
select name, created_at from vault.secrets where name like 'smartgym_%' order by name;
"@
  try {
    [System.IO.File]::WriteAllText($tmpSql, $sql, (New-Object System.Text.UTF8Encoding $false))
    npx supabase db query --linked -f $tmpSql
    if ($LASTEXITCODE -ne 0) { Fail 'vault secrets' }
  } finally {
    Remove-Item $tmpSql -Force -ErrorAction SilentlyContinue
  }

  Step 'Supabase: deploy Edge Functions (server-side bundling, no Docker)'
  foreach ($fn in @('ingest-events', 'device-sync', 'notification-dispatch')) {
    npx supabase functions deploy $fn --use-api --no-verify-jwt
    if ($LASTEXITCODE -ne 0) { Fail "deploy $fn" }
  }
  npx supabase functions deploy qr-token --use-api
  if ($LASTEXITCODE -ne 0) { Fail 'deploy qr-token' }

  Step 'Supabase: review and push declared config.toml settings (auth, storage)'
  npx supabase config diff
  npx supabase config push --yes
  if ($LASTEXITCODE -ne 0) { Fail 'config push' }

  Step 'Supabase: verify'
  npx supabase migration list --linked
  npx supabase functions list --project-ref $ref
  npx supabase secrets list
  npx supabase db query --linked "select jobname, schedule, active from cron.job order by jobname"
}

if (-not $SkipGitHub) {
  Step 'GitHub: make gh the git credential helper for github.com'
  gh auth setup-git
  if ($LASTEXITCODE -ne 0) { Fail 'gh auth setup-git (run: gh auth login --web)' }

  $dirty = git status --porcelain
  if ($dirty) { Fail 'Working tree is not clean; commit or stash first.' }

  git remote get-url origin *> $null
  if ($LASTEXITCODE -ne 0) {
    Step "GitHub: create private repository '$RepoName'"
    gh repo create $RepoName --private --source . --remote origin `
      --description 'AI Smart Gym: consent-based CCTV workout tracking (edge AI + Supabase + Next.js)'
    if ($LASTEXITCODE -ne 0) { Fail 'gh repo create' }
  }

  Step 'GitHub: push main (requires the workflow scope: gh auth refresh -s workflow)'
  git push -u origin main
  if ($LASTEXITCODE -ne 0) { Fail 'git push' }

  Step 'GitHub: CI'
  gh run list --limit 3
  Write-Host 'Watch with: gh run watch' -ForegroundColor Yellow
}

Write-Host "`nDone. Live verification (QA tenant, removed afterwards):" -ForegroundColor Green
Write-Host '  node tests/integration/live-owner-login.mjs <owner e-mail>'
Write-Host '  npm run test:live -w tests/live      # authenticated functions + end-to-end session incl. push delivery'
Write-Host '  npm run test:ui -w tests/live        # Playwright click-through of both deployed apps'
