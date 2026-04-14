"""
ALICE Brain — Point d'entrée FastAPI (main.py)
──────────────────────────────────────────────
Démarre l'API, configure le scheduler APScheduler pour le polling
toutes les DETECTION_POLL_INTERVAL secondes, et expose tous les endpoints.

Usage :
  uvicorn main:app --host 0.0.0.0 --port 8000 --reload
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from services.elasticsearch import es_service
from agents.detection import detect_and_inject

from api.incidents import router as incidents_router
from api.actions import router as actions_router
from api.websocket import router as ws_router

# ── Logging ──
logging.basicConfig(
    level=settings.LOG_LEVEL if hasattr(settings, "LOG_LEVEL") else "INFO",
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ── Scheduler ──
scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle : démarrage et arrêt de l'application."""
    logger.info("═" * 60)
    logger.info("  ALICE Brain — Starting...")
    logger.info("  Simulation mode: %s", settings.ALICE_SIMULATION_MODE)
    logger.info("  ES URL: %s", settings.ES_URL)
    logger.info("  Detection poll: every %ds", settings.DETECTION_POLL_INTERVAL)
    logger.info("═" * 60)

    # Démarrer le polling de détection
    scheduler.add_job(
        detect_and_inject,
        "interval",
        seconds=settings.DETECTION_POLL_INTERVAL,
        id="detection_poll",
        max_instances=1,
    )
    scheduler.start()

    yield

    # Shutdown
    logger.info("ALICE Brain — Shutting down...")
    scheduler.shutdown(wait=False)
    await es_service.close()


# ── App FastAPI ──
app = FastAPI(
    title="ALICE Brain API",
    description="Advanced Learning Intelligence for Cybersecurity Events — Multi-Agent SOC Brain",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routes ──
app.include_router(incidents_router)
app.include_router(actions_router)
app.include_router(ws_router)


@app.get("/api/health", tags=["health"])
async def health_check():
    """Vérifie l'état de l'application et de ses dépendances."""
    es_ok = await es_service.check_health()
    return {
        "status": "ok",
        "elasticsearch": "connected" if es_ok else "disconnected",
        "simulation_mode": settings.ALICE_SIMULATION_MODE,
        "llm_primary": "MiniMax M2.7 (NVIDIA NIM)",
        "llm_fallback": "Claude claude-sonnet-4-5" if settings.ANTHROPIC_API_KEY else "disabled",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
