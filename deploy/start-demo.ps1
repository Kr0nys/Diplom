# Demo mode: Docker on port 8080, optional Tuna tunnel.
# Usage: .\deploy\start-demo.ps1
# Requires: Docker Desktop

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "=== Python Test Gen - DEMO ===" -ForegroundColor Cyan
Write-Host ""

function Test-DockerReady {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: docker not found in PATH." -ForegroundColor Red
        Write-Host "  Restart the terminal after installing Docker Desktop," -ForegroundColor Yellow
        Write-Host "  or run this script from Docker Desktop PowerShell." -ForegroundColor Yellow
        return $false
    }

    $prevEap = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    $dockerOut = docker info 2>&1
    $code = $LASTEXITCODE
    $ErrorActionPreference = $prevEap

    if ($code -ne 0) {
        Write-Host "ERROR: Docker daemon not reachable (exit code $code)." -ForegroundColor Red
        Write-Host "  Start Docker Desktop and wait until status is Running." -ForegroundColor Yellow
        if ($dockerOut) {
            Write-Host "  Details:" -ForegroundColor Yellow
            $dockerOut | Select-Object -First 6 | ForEach-Object { Write-Host "    $_" }
        }
        return $false
    }

    return $true
}

if (-not (Test-DockerReady)) {
    exit 1
}

Write-Host "[1/2] Starting containers, nginx on port 8080..." -ForegroundColor Yellow
$ErrorActionPreference = "Continue"
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build
$composeCode = $LASTEXITCODE
$ErrorActionPreference = "Stop"

if ($composeCode -ne 0) {
    Write-Host "ERROR: docker compose failed (exit code $composeCode)." -ForegroundColor Red
    exit 1
}

Write-Host "[2/2] Waiting for services, about 20 seconds..." -ForegroundColor Yellow
Start-Sleep -Seconds 20

Write-Host ""
Write-Host "=== Local ===" -ForegroundColor Cyan
try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8080" -UseBasicParsing -TimeoutSec 10
    Write-Host "  Site: http://127.0.0.1:8080 (HTTP $($r.StatusCode))" -ForegroundColor Green
} catch {
    Write-Host "  Site: http://127.0.0.1:8080 (not ready yet, try in a minute)" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "=== Public access ===" -ForegroundColor Cyan
Write-Host "  A) Same Wi-Fi: http://YOUR_PC_IP:8080  (ipconfig -> IPv4)"
Write-Host "  B) Internet:   .\deploy\start-tunnel-tuna.ps1"
Write-Host "                 -> https://python-test-gen.tuna.am"
Write-Host ""
Write-Host "Stop: .\deploy\stop-demo.ps1" -ForegroundColor Cyan
