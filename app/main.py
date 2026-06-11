import logging
import sys
import uvicorn
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded

from .config import CORS_ORIGINS
from .database import init_db, get_pool
from .routers import auth, files, recovery, events
from .limiter import limiter
from .services.auto_sync_romm import auto_sync_loop
from .services.romm_client import romm_client

# --- Logging ---
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("VaultSync")

_background_tasks: set[asyncio.Task] = set()


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        init_db()
    except Exception as e:
        logger.critical(f"❌ Startup failed — cannot initialize DB: {e}")
        sys.exit(1)

    task = asyncio.create_task(auto_sync_loop(), name="auto_sync_romm")
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)

    yield

    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    await romm_client.close()

    pool = get_pool()
    if pool:
        pool.closeall()
        logger.info("📡 Database connection pool closed")


app = FastAPI(title="VaultSync Server", version="1.5.1", lifespan=lifespan)

# --- Rate Limiting ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# --- Middleware ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True if "*" not in CORS_ORIGINS else False,
    allow_methods=["GET", "POST", "DELETE"],
    allow_headers=["*"],
)

# --- Routers ---
app.include_router(auth.router)
app.include_router(files.router)
app.include_router(recovery.router)
app.include_router(events.router, prefix="/api/v1")

@app.get("/")
def health_check():
    return {
        "status": "online", 
        "version": "VaultSync-v1.5.1-Modular",
        "database": "connected" if get_pool() else "disconnected"
    }

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
