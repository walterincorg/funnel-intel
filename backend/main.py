import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import GIT_COMMIT
from backend.routers import competitors, scans, pricing, compare, ads

log = logging.getLogger(__name__)
STARTUP_TIME = datetime.now(timezone.utc).isoformat()


def _worker_supervisor():
    """Runs the worker loop and restarts it if it crashes."""
    from backend.worker.loop import main as worker_main
    while True:
        try:
            worker_main()
        except Exception:
            log.exception("Worker crashed — restarting in 5s")
            time.sleep(5)


@asynccontextmanager
async def lifespan(app: FastAPI):
    t = threading.Thread(target=_worker_supervisor, daemon=True, name="worker")
    t.start()
    yield


app = FastAPI(title="Funnel Intel", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(competitors.router)
app.include_router(scans.router)
app.include_router(pricing.router)
app.include_router(compare.router)
app.include_router(ads.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/version")
def version():
    return {"commit": GIT_COMMIT, "deployed_at": STARTUP_TIME}


# Serve frontend static files in production
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")
