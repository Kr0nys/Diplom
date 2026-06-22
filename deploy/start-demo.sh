#!/usr/bin/env bash
# Демо на Linux/macOS. Windows: deploy/start-demo.ps1
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Python Test Gen - DEMO ==="
docker compose -f docker-compose.yml -f docker-compose.demo.yml up -d --build
echo "Ожидание 20 сек..."
sleep 20
echo ""
echo "Локально:  http://127.0.0.1:8080"
echo "Интернет:  tuna http 8080 --subdomain=python-test-gen"
echo "           -> https://python-test-gen.tuna.am"
echo "           (после tuna login; другой субдомен: TUNA_SUBDOMAIN=...)"
echo "           или Wi‑Fi: http://IP_ВАШЕГО_ПК:8080"
echo "Остановка: docker compose -f docker-compose.yml -f docker-compose.demo.yml down"
