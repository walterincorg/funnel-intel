#!/bin/bash
set -e

APP_DIR="/opt/funnel-intel"
cd "$APP_DIR"

echo "=== Pulling latest code ==="
git pull origin main

echo "=== Installing Python dependencies ==="
.venv/bin/pip install -r requirements.txt --quiet

echo "=== Building frontend ==="
cd frontend
npm ci --silent
npm run build
cd ..

echo "=== Restarting services ==="
# Legacy single-worker unit is masked; use the funnel-worker@ template instances.
sudo systemctl restart funnel-dashboard funnel-worker@1.service funnel-worker@2.service funnel-worker@3.service

echo "=== Deploy complete ==="
echo "Commit: $(git rev-parse --short HEAD)"
echo "Time:   $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# Health check — retry to tolerate uvicorn startup race
for i in $(seq 1 15); do
  if curl -sf http://127.0.0.1:4318/api/health > /dev/null; then
    echo "Health check: OK (attempt $i)"
    exit 0
  fi
  sleep 1
done
echo "Health check: FAILED after 15s"
exit 1
