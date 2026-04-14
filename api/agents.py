"""
ALICE Brain — API Agents (api/agents.py)
────────────────────────────────────────
Endpoints REST pour la gestion dynamique des Alice-Agents :
  - POST   /api/agents/register              — enregistrement
  - GET    /api/agents                        — liste tous les agents
  - GET    /api/agents/{agent_id}             — détail d'un agent
  - GET    /api/agents/{agent_id}/metrics     — métriques temps réel
  - DELETE /api/agents/{agent_id}             — désenregistrement manuel
  - POST   /api/actions/{action_id}/result    — fallback HTTP pour les résultats
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services.agent_registry import agent_registry
from services.agent_communicator import agent_communicator

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["agents"])


# ───────────────────────────────────────
#  MODÈLES DE REQUÊTE / RÉPONSE
# ───────────────────────────────────────

class AgentRegistrationRequest(BaseModel):
    """Payload d'enregistrement envoyé par un Alice-Agent au démarrage."""
    agent_id: str
    hostname: str = "unknown"
    ip: str = "0.0.0.0"
    os: str = "unknown"
    sub_agents: dict[str, str] = {}       # "endpoint" → "http://x.x.x.x:8001"
    capabilities: list[str] = []
    registered_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class AgentRegistrationResponse(BaseModel):
    """Réponse du Brain après enregistrement réussi."""
    status: str = "registered"
    agent_token: str
    brain_ws_url: str
    heartbeat_interval: int = 30


class ActionResultRequest(BaseModel):
    """Résultat d'exécution envoyé par un agent via HTTP fallback."""
    action_id: str
    agent_id: str
    sub_agent: str = "unknown"
    success: bool
    output: str = ""
    error: str | None = None
    duration_ms: int = 0
    executed_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    simulated: bool = False


# ───────────────────────────────────────
#  ENDPOINTS
# ───────────────────────────────────────

@router.post("/agents/register", response_model=AgentRegistrationResponse)
async def register_agent(body: AgentRegistrationRequest):
    """
    Enregistre un Alice-Agent auprès du Brain.

    L'agent reçoit en retour un token de session et l'URL WebSocket
    à utiliser pour la connexion persistante.
    """
    logger.info(
        "Registration request from agent: id=%s, hostname=%s, ip=%s",
        body.agent_id, body.hostname, body.ip,
    )

    result = await agent_registry.register(body.model_dump())

    return AgentRegistrationResponse(
        status=result["status"],
        agent_token=result["agent_token"],
        brain_ws_url=result["brain_ws_url"],
        heartbeat_interval=result["heartbeat_interval"],
    )


@router.get("/agents")
async def list_agents(status: str | None = None):
    """
    Liste tous les agents enregistrés avec leur statut.

    Query params:
      ?status=online|offline|unreachable  — filtre optionnel par statut
    """
    agents = agent_registry.get_all_agents()

    if status:
        agents = [a for a in agents if a.status == status]

    return [
        {
            "agent_id": a.agent_id,
            "hostname": a.hostname,
            "ip": a.ip,
            "os": a.os,
            "status": a.status,
            "capabilities": a.capabilities,
            "sub_agents": a.sub_agents,
            "last_heartbeat": a.last_heartbeat.isoformat(),
            "registered_at": a.registered_at,
            "has_ws": a.ws_connection is not None,
        }
        for a in agents
    ]


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str):
    """Détail complet d'un agent spécifique."""
    agent = agent_registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    return {
        "agent_id": agent.agent_id,
        "hostname": agent.hostname,
        "ip": agent.ip,
        "os": agent.os,
        "status": agent.status,
        "capabilities": agent.capabilities,
        "sub_agents": agent.sub_agents,
        "last_heartbeat": agent.last_heartbeat.isoformat(),
        "registered_at": agent.registered_at,
        "has_ws": agent.ws_connection is not None,
        "metrics": agent.metrics.model_dump(),
    }


@router.get("/agents/{agent_id}/metrics")
async def get_agent_metrics(agent_id: str):
    """Métriques CPU/RAM/disk en temps réel d'un agent."""
    agent = agent_registry.get_agent(agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    return {
        "agent_id": agent.agent_id,
        "status": agent.status,
        "last_heartbeat": agent.last_heartbeat.isoformat(),
        "metrics": agent.metrics.model_dump(),
    }


@router.delete("/agents/{agent_id}")
async def unregister_agent(agent_id: str):
    """Désenregistrement manuel d'un agent."""
    removed = await agent_registry.unregister(agent_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' not found")

    logger.info("Agent %s manually unregistered", agent_id)
    return {"status": "unregistered", "agent_id": agent_id}


@router.post("/actions/{action_id}/result")
async def receive_action_result(action_id: str, body: ActionResultRequest):
    """
    Endpoint HTTP fallback pour recevoir les résultats d'exécution
    quand la connexion WebSocket de l'agent est indisponible.
    """
    logger.info(
        "HTTP result received for action %s from agent %s (success=%s)",
        action_id, body.agent_id, body.success,
    )

    # Traiter le résultat via le communicator (même logique que le WS)
    message = {
        "type": "action_result",
        "action_id": action_id,
        "agent_id": body.agent_id,
        "sub_agent": body.sub_agent,
        "success": body.success,
        "output": body.output,
        "error": body.error,
        "duration_ms": body.duration_ms,
        "executed_at": body.executed_at,
        "simulated": body.simulated,
    }
    await agent_communicator.handle_agent_message(body.agent_id, message)

    return {"status": "received", "action_id": action_id}
