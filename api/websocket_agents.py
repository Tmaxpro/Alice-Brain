"""
ALICE Brain — WebSocket Agents Handler (api/websocket_agents.py)
───────────────────────────────────────────────────────────────
Route WebSocket dédiée aux connexions des Alice-Agents.

Séparé du WS dashboard (/ws/incidents) — ce endpoint gère :
  - Connexion persistante de chaque agent
  - Réception des heartbeats, ACKs, et résultats d'actions
  - Dispatch des messages via agent_communicator
  - Nettoyage à la déconnexion
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

from services.agent_registry import agent_registry
from services.agent_communicator import agent_communicator

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws/agent/{agent_id}")
async def agent_websocket(websocket: WebSocket, agent_id: str, token: str = Query("")):
    """
    Connexion WebSocket persistante d'un Alice-Agent.

    L'agent se connecte après l'enregistrement HTTP et maintient cette
    connexion ouverte pour :
      - Recevoir des ordres en temps réel (execute_action)
      - Envoyer des heartbeats périodiques
      - Remonter les résultats d'actions
      - Envoyer des ACK de réception

    Authentification : le token obtenu lors de l'enregistrement est
    passé en query parameter.

    URL : ws://{brain_host}:8000/ws/agent/{agent_id}?token={agent_token}
    """
    # ── Vérifier que l'agent est enregistré ──
    agent = agent_registry.get_agent(agent_id)
    if not agent:
        logger.warning("WS connection rejected: agent '%s' not registered", agent_id)
        await websocket.close(code=4001, reason="Agent not registered")
        return

    # ── Vérifier le token ──
    if token and not agent_registry.validate_token(agent_id, token):
        logger.warning("WS connection rejected: invalid token for agent '%s'", agent_id)
        await websocket.close(code=4003, reason="Invalid token")
        return

    # ── Accepter la connexion ──
    await websocket.accept()
    agent_registry.set_ws_connection(agent_id, websocket)
    logger.info("Agent WS connected: %s (hostname=%s, ip=%s)", agent_id, agent.hostname, agent.ip)

    try:
        while True:
            # Attendre un message de l'agent
            raw = await websocket.receive_text()

            try:
                message = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("Invalid JSON from agent %s: %s", agent_id, raw[:200])
                continue

            # Dispatcher le message au communicator
            response = await agent_communicator.handle_agent_message(agent_id, message)

            # Envoyer la réponse si le handler en a produit une (ex: heartbeat_ack)
            if response is not None:
                await websocket.send_text(json.dumps(response, default=str))

    except WebSocketDisconnect:
        logger.info("Agent WS disconnected: %s", agent_id)
    except Exception as exc:
        logger.error("Agent WS error for %s: %s", agent_id, exc)
    finally:
        # Nettoyer la référence WS dans le registre
        agent_registry.clear_ws_connection(agent_id)
        logger.info("Agent WS connection cleaned up: %s", agent_id)
