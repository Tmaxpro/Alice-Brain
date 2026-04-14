import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from config import settings
from services.elasticsearch import es_service
from agents.orchestrator import orchestrator
from agents.detection import detection_agent

from api.incidents import router as incidents_router
from api.actions import router as actions_router
from api.websocket import router as ws_router

logging.basicConfig(level=settings.LOG_LEVEL)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()

async def poll_elasticsearch_task():
    """Tâche périodique qui poll ES toutes les 30s."""
    logger.info("Polling Elasticsearch for new logs...")
    logs = await es_service.get_recent_failed_logins(minutes=5)
    if logs:
        logger.info(f"Found {len(logs)} potential brute force logs.")
        alerts = await detection_agent.detect_anomalies(logs)
        for alarm in alerts:
            # Envoie à l'orchestrateur
            await orchestrator.process_new_alert(alarm)
    else:
        logger.info("No suspicious logs found.")

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Démarrage
    logger.info("Starting ALICE Brain...")
    # Lancement du scheduler
    scheduler.add_job(poll_elasticsearch_task, 'interval', seconds=30)
    scheduler.start()
    
    yield
    # Extinction
    logger.info("Shutting down ALICE Brain...")
    scheduler.shutdown()
    await es_service.client.close()

app = FastAPI(title="ALICE Brain API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routes
app.include_router(incidents_router)
app.include_router(actions_router)
app.include_router(ws_router)

@app.get("/api/health")
async def health_check():
    es_status = await es_service.check_health()
    return {
        "status": "ok",
        "elasticsearch": "connected" if es_status else "disconnected"
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
