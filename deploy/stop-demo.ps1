# Stop demo stack

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

Write-Host "Stopping containers..." -ForegroundColor Cyan
docker compose -f docker-compose.yml -f docker-compose.demo.yml down
Write-Host "Done." -ForegroundColor Green
