import logging
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from backend.config import GIT_COMMIT
from backend.logging_config import setup_logging
from backend.middleware import RequestLoggingMiddleware
from backend.routers import competitors, scans, pricing, compare, ads, domains, settings

# Initialize logging before anything else
setup_logging()

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
    log.info("Funnel Intel starting up — commit=%s", GIT_COMMIT)
    t = threading.Thread(target=_worker_supervisor, daemon=True, name="worker")
    t.start()
    log.info("Worker supervisor thread started")
    yield
    log.info("Funnel Intel shutting down")


app = FastAPI(title="Funnel Intel", version="0.1.0", lifespan=lifespan)

app.add_middleware(RequestLoggingMiddleware)
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
app.include_router(domains.router)
app.include_router(settings.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/version")
def version():
    return {"commit": GIT_COMMIT, "deployed_at": STARTUP_TIME}


class SPAStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404:
                return FileResponse(Path(self.directory) / "index.html")
            raise


# Serve frontend static files in production
frontend_dist = Path(__file__).parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    app.mount("/", SPAStaticFiles(directory=str(frontend_dist), html=True), name="frontend")
