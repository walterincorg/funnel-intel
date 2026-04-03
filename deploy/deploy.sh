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
sudo systemctl restart funnel-dashboard funnel-worker

echo "=== Deploy complete ==="
echo "Commit: $(git rev-parse --short HEAD)"
echo "Time:   $(date -u '+%Y-%m-%d %H:%M:%S UTC')"

# Health check
sleep 2
if curl -sf http://127.0.0.1:4318/api/health > /dev/null; then
  echo "Health check: OK"
else
  echo "Health check: FAILED"
  exit 1
fi
