# VPS Configuration Guide

How to set up a clean Hostinger Ubuntu VPS to run Funnel Intel.

**Current VPS:** `187.124.241.54` (root SSH)  
**Code path:** `/opt/funnel-intel`  
**Dashboard port:** `4318` (loopback only — tunnel via `ssh -L 4318:127.0.0.1:4318 root@187.124.241.54`)

---

## 1. System packages

```bash
apt update && apt upgrade -y
apt install -y python3.12 python3.12-venv python3-pip git nodejs npm xvfb curl
```

---

## 2. Clone the repo and install dependencies

```bash
mkdir -p /opt/funnel-intel
cd /opt/funnel-intel
git clone https://github.com/<your-repo>.git .

python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

cd frontend && npm ci && npm run build && cd ..
```

---

## 3. Environment file

```bash
cp .env.example .env
nano .env   # fill in secrets
```

Key settings for VPS production:

| Variable | VPS value | Notes |
|---|---|---|
| `LLM_PROVIDER` | `anthropic` | |
| `LLM_MODEL` | `claude-opus-4-5` | |
| `ANTHROPIC_API_KEY` | `sk-ant-...` | |
| `SUPABASE_URL` | `https://....supabase.co` | |
| `SUPABASE_SERVICE_ROLE_KEY` | `eyJ...` | |
| `SUPABASE_STORAGE_BUCKET` | `funnel-screenshots` | |
| `BROWSER_HEADLESS` | `false` | Many quiz funnels block headless Chrome |
| `DISPLAY` | `:99` | Points at Xvfb — **required when `BROWSER_HEADLESS=false`** |
| `BROWSER_USE_LOGGING_LEVEL` | `result` | Reduces log noise |
| `APIFY_API_TOKEN` | `apify_api_...` | For ad scraping |
| `APIFY_ADS_ACTOR_ID` | `curious_coder/facebook-ads-library-scraper` | |
| `AD_SCRAPE_HOUR_UTC` | `6` | Hour to run daily ad scrape |
| `AD_SCRAPE_DAYS_OF_WEEK` | `0,3` | Mon + Thu |
| `WHOISXML_API_KEY` | `...` | For domain intel |
| `OPENCLAW_TELEGRAM_TARGET` | `@channel` or chat ID | Alert destination |
| `LOG_LEVEL` | `INFO` | |
| `LOG_FILE` | `/var/log/funnel-intel/app.log` | Optional rotating file log |

---

## 4. Xvfb (virtual display for headed Chrome)

Headed Chrome is required when `BROWSER_HEADLESS=false`. Xvfb provides a virtual
framebuffer so Chrome can render without a physical screen.

**Install the systemd unit:**

```bash
cp /opt/funnel-intel/deploy/xvfb.service /etc/systemd/system/xvfb.service
systemctl daemon-reload
systemctl enable xvfb
systemctl start xvfb
systemctl status xvfb
```

Verify it's running on `:99`:
```bash
DISPLAY=:99 xdpyinfo | head -5
```

> **Why this matters:** without `DISPLAY=:99` in `.env`, Chrome launches but immediately
> exits with "Missing X server or $DISPLAY" / "The platform failed to initialize".
> systemd services don't inherit the shell's `$DISPLAY`, so it must be in the `.env`.

---

## 5. Systemd service units

All service files live in `deploy/`. Copy them on first setup or after changes:

```bash
cp /opt/funnel-intel/deploy/funnel-worker@.service  /etc/systemd/system/
cp /opt/funnel-intel/deploy/funnel-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable funnel-dashboard funnel-worker@1 funnel-worker@2 funnel-worker@3
systemctl start  funnel-dashboard funnel-worker@1 funnel-worker@2 funnel-worker@3
```

### `funnel-worker@.service`

Key settings and why:

| Setting | Value | Reason |
|---|---|---|
| `Restart=always` | always | Workers intentionally call `sys.exit(0)` after each scan to get a clean process (avoids bubus/asyncio state accumulation between scans) |
| `RestartSec=2` | 2s | Short gap so the worker picks up the next job quickly after the clean exit |
| `WORKER_ID=%i` | instance number | Instance `1` is primary — runs cleanup on startup and background ad/domain loops |

### `funnel-dashboard.service`

Runs uvicorn on `127.0.0.1:4318`. Access via SSH tunnel:

```bash
ssh -L 4318:127.0.0.1:4318 root@187.124.241.54
# then open http://localhost:4318
```

---

## 6. Playwright / Chromium

browser-use needs Playwright's Chromium build:

```bash
PLAYWRIGHT_BROWSERS_PATH=/root/.cache/ms-playwright \
  .venv/bin/python -m playwright install chromium
```

Verify:
```bash
ls /root/.cache/ms-playwright/chromium-*/chrome-linux64/chrome
```

---

## 7. Deploy updates

After pushing to `main`:

```bash
ssh root@187.124.241.54 "cd /opt/funnel-intel && bash deploy/deploy.sh"
```

Or manually:

```bash
ssh root@187.124.241.54 "cd /opt/funnel-intel && git pull && \
  systemctl restart funnel-dashboard funnel-worker@1 funnel-worker@2 funnel-worker@3"
```

**If you changed a systemd unit file** (anything in `deploy/*.service`), also run:

```bash
ssh root@187.124.241.54 "cp /opt/funnel-intel/deploy/funnel-worker@.service /etc/systemd/system/ && \
  systemctl daemon-reload && \
  systemctl restart funnel-worker@1 funnel-worker@2 funnel-worker@3"
```

---

## 8. Logs

```bash
# All workers combined
journalctl -u 'funnel-worker@*' -f

# Specific worker
journalctl -u funnel-worker@1 -f

# Dashboard
journalctl -u funnel-dashboard -f

# Last 100 lines from all workers
journalctl -u 'funnel-worker@*' -n 100 --no-pager
```

---

## 9. Troubleshooting

### Chrome won't start / CDP timeout (30s)

1. Check `DISPLAY` is set in `.env` and Xvfb is running:
   ```bash
   systemctl status xvfb
   echo $DISPLAY   # should print :99 in the worker environment
   ```
2. Check Pydantic version: `pip show pydantic` — if 2.12+, `user_data_dir` must be
   passed explicitly in `traversal.py` (already fixed in code).
3. Kill any zombie Chrome processes: `pkill -f chrome`
4. Clean up leftover temp dirs: `rm -rf /tmp/funnel-scan-* /tmp/browser_use_agent_*`

### Scans fail on the second run in the same worker process

This is the bubus EventBus stale asyncio state bug. Fixed by `sys.exit(0)` in
`backend/worker/loop.py` — workers always exit after one scan and systemd restarts
them. If you see repeated failures, verify `Restart=always` and `RestartSec=2` are
in the live unit file (`systemctl cat funnel-worker@1`).

### Stale "running" scan_runs after a worker crash

The primary worker (ID=1) runs `cleanup_stale_jobs()` on startup, which marks any
scan_run older than 45 min as failed. For immediate cleanup:

```bash
cd /opt/funnel-intel
.venv/bin/python3 << 'EOF'
from backend.db import get_db
from datetime import datetime, timezone
db = get_db()
now = datetime.now(timezone.utc).isoformat()
cutoff = '2026-01-01T00:00:00+00:00'  # adjust as needed
stale = db.table('scan_runs').select('id').eq('status','running').lt('started_at', cutoff).execute().data
for r in stale:
    db.table('scan_runs').update({'status':'failed','completed_at':now,'summary':{'error':'Manual cleanup'}}).eq('id', r['id']).execute()
print(f'Cleaned {len(stale)} stale runs')
EOF
```

### Check worker health at a glance

```bash
cd /opt/funnel-intel && .venv/bin/python3 << 'EOF'
from backend.db import get_db
from collections import Counter
db = get_db()
jobs = db.table('scan_jobs').select('status').execute().data
print('Jobs:', dict(Counter(j['status'] for j in jobs)))
EOF
```
