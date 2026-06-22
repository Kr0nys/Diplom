# Install Tuna CLI to deploy\tuna.exe (https://tuna.am)
# Then: tuna login  and  .\deploy\start-tunnel-tuna.ps1

$ErrorActionPreference = "Continue"
$DeployDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$LocalTuna = Join-Path $DeployDir "tuna.exe"

if (Get-Command tuna -ErrorAction SilentlyContinue) {
    Write-Host "tuna already in PATH:" -ForegroundColor Green
    tuna version 2>$null; tuna help 2>$null | Select-Object -First 1
    exit 0
}

if (Test-Path $LocalTuna) {
    Write-Host "Found: $LocalTuna" -ForegroundColor Green
    & $LocalTuna version
    exit 0
}

Write-Host "Downloading Tuna CLI to deploy\tuna.exe ..." -ForegroundColor Cyan
Write-Host "Source: https://releases.tuna.am" -ForegroundColor Cyan

$zipUrl = "https://releases.tuna.am/tuna/latest/tuna_windows_amd64.zip"
$zipPath = Join-Path $env:TEMP "tuna_windows_amd64.zip"

try {
    Invoke-WebRequest -Uri $zipUrl -OutFile $zipPath -UseBasicParsing -TimeoutSec 180
    Expand-Archive -Path $zipPath -DestinationPath $DeployDir -Force
    $extracted = Join-Path $DeployDir "tuna.exe"
    if (-not (Test-Path $extracted)) {
        $inner = Get-ChildItem -Path $DeployDir -Filter "tuna.exe" -Recurse -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($inner) { Copy-Item $inner.FullName $LocalTuna -Force }
    }
    if (Test-Path $LocalTuna) {
        Write-Host "OK: $LocalTuna" -ForegroundColor Green
        & $LocalTuna version
        Write-Host ""
        Write-Host "Next: tuna login  (browser opens, register at tuna.am)" -ForegroundColor Yellow
        exit 0
    }
} catch {
    Write-Host "Download failed: $($_.Exception.Message)" -ForegroundColor Red
}

Write-Host ""
Write-Host "Manual install:" -ForegroundColor Cyan
Write-Host "  1. https://tuna.am - Tuna Desktop or MSI from https://tuna.am/releases/"
Write-Host "  2. Or: winget install --id yuccastream.tuna"
Write-Host "  3. tuna login"
Write-Host "  4. .\deploy\start-tunnel-tuna.ps1"
exit 1
