# Tuna tunnel with fixed subdomain (https://tuna.am).
# Once: .\deploy\install-tuna.ps1  then  tuna login
# Demo stack must listen on http://127.0.0.1:8080
#
# Subdomain: env TUNA_SUBDOMAIN or python-test-gen
# Public URL: https://python-test-gen.tuna.am

$ErrorActionPreference = "Continue"
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalTuna = Join-Path $DeployDir "tuna.exe"
$TunaPort = if ($env:TUNA_PORT) { $env:TUNA_PORT } else { "8080" }
$TunaSubdomain = if ($env:TUNA_SUBDOMAIN) { $env:TUNA_SUBDOMAIN } else { "python-test-gen" }
$PublicUrl = "https://${TunaSubdomain}.tuna.am"

try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:$TunaPort" -UseBasicParsing -TimeoutSec 5
    Write-Host "Local site OK: http://127.0.0.1:$TunaPort ($($r.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "ERROR: start demo first (port $TunaPort):" -ForegroundColor Red
    Write-Host "  docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d" -ForegroundColor Yellow
    exit 1
}

$tunaExe = $null
if (Test-Path $LocalTuna) { $tunaExe = $LocalTuna }
else {
    $cmd = Get-Command tuna -ErrorAction SilentlyContinue
    if ($cmd) { $tunaExe = $cmd.Source }
}

if (-not $tunaExe) {
    Write-Host "tuna not found. Run: .\deploy\install-tuna.ps1" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Tuna: port $TunaPort -> $PublicUrl ===" -ForegroundColor Cyan
Write-Host "Public URL: $PublicUrl" -ForegroundColor Green
Write-Host "Command: tuna http $TunaPort --subdomain=$TunaSubdomain" -ForegroundColor DarkGray
Write-Host "Ctrl+C stops tunnel only; Docker keeps running." -ForegroundColor Cyan
Write-Host ""

& $tunaExe http $TunaPort --subdomain=$TunaSubdomain
