"""
ALICE Brain — Agent Communicator (services/agent_communicator.py)
────────────────────────────────────────────────────────────────
Gère l'envoi d'ordres aux Alice-Agents via deux canaux :
  1. WebSocket (canal principal, temps réel)
  2. HTTP REST (canal fallback si le WS est déconnecté)

Implémente aussi le traitement des messages entrants des agents
(heartbeat, action_result, ack) et le routage intelligent des actions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import httpx

from config import settings
from services.agent_registry import agent_registry, AgentInfo
from services.elasticsearch import es_service
from services.websocket_manager import ws_manager

logger = logging.getLogger(__name__)

# ───────────────────────────────────────
#  Mapping des noms d'agents legacy → types sub-agent
# ───────────────────────────────────────

LEGACY_AGENT_TO_SUBAGENT: dict[str, str] = {
    "endpoint_agent": "endpoint",
    "network_agent": "network",
    "log_collector": "collector",
    "notif_agent": "notif",
}


class NoAgentAvailableError(Exception):
    """Aucun agent disponible pour exécuter l'action demandée."""
    pass


# ───────────────────────────────────────
#  COMMUNICATOR
# ───────────────────────────────────────

class AgentCommunicator:
    """
    Orchestre la communication Brain → Agent.

    - Envoi via WebSocket avec timeout ACK de 5 secondes
    - Fallback HTTP POST si le WS est indisponible ou sans ACK
    - Traitement des messages agents (heartbeat, action_result, ack)
    - Routage intelligent : IP exacte → même subnet → capability match
    """

    def __init__(self) -> None:
        # action_id → asyncio.Event : déclenché quand l'ACK est reçu
        self._pending_acks: dict[str, asyncio.Event] = {}
        # action_id → résultat reçu par l'agent
        self._action_results: dict[str, dict[str, Any]] = {}
        # action_id → asyncio.Event : déclenché quand le résultat arrive
        self._result_events: dict[str, asyncio.Event] = {}

    # ──────────── Routage Intelligent ────────────

    async def route_action(
        self,
        action_type: str,
        target_agent_name: str,
        params: dict[str, Any],
        incident_source_ip: str | None = None,
    ) -> AgentInfo:
        """
        Sélectionne le meilleur Alice-Agent pour exécuter une action.

        Stratégie :
          1. Agent installé sur la machine cible (IP exacte depuis params ou incident)
          2. Agent dans le même sous-réseau /24
          3. N'importe quel agent online avec la capability requise
        """
        # Extraire l'IP cible des params ou de l'incident
        target_ip = params.get("ip") or params.get("target_ip") or incident_source_ip

        # Mapper le nom legacy vers le type sub-agent
        sub_agent_type = LEGACY_AGENT_TO_SUBAGENT.get(target_agent_name, target_agent_name)

        if target_ip:
            agent = agent_registry.get_best_agent_for_target(target_ip, action_type)
            if agent:
                return agent

        # Fallback : n'importe quel agent avec la capability
        agents = agent_registry.get_agents_by_capability(action_type)
        if agents:
            return agents[0]

        # Fallback ultime : n'importe quel agent online
        online = agent_registry.get_online_agents()
        if online:
            return online[0]

        raise NoAgentAvailableError(
            f"Aucun agent disponible pour '{action_type}' "
            f"(target_agent={target_agent_name}, target_ip={target_ip})"
        )

    # ──────────── Envoi d'actions ────────────

    async def send_action(
        self,
        agent: AgentInfo,
        action_id: str,
        action_type: str,
        target_sub_agent: str,
        params: dict[str, Any],
        priority: str = "NORMAL",
        timeout: int = 30,
    ) -> dict[str, Any]:
        """
        Envoie un ordre à un agent via WS (principal) ou HTTP (fallback).

        Retourne un dict :
          {"delivered": True/False, "channel": "ws"|"http"|"none", "error": str|None}
        """
        # Mapper le nom legacy vers le type sub-agent
        sub_agent_type = LEGACY_AGENT_TO_SUBAGENT.get(target_sub_agent, target_sub_agent)

        payload = {
            "type": "execute_action",
            "action_id": action_id,
            "action_type": action_type,
            "target_sub_agent": sub_agent_type,
            "params": params,
            "priority": priority,
            "timeout": timeout,
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "requires_ack": True,
        }

        # ── Canal 1 : WebSocket ──
        if agent.ws_connection is not None:
            ws_result = await self._send_via_ws(agent, payload, action_id)
            if ws_result:
                return {"delivered": True, "channel": "ws", "error": None}
            logger.warning(
                "WS delivery failed/no ACK for action %s on agent %s — falling back to HTTP",
                action_id, agent.agent_id,
            )

        # ── Canal 2 : HTTP REST (fallback) ──
        sub_agent_url = agent.sub_agents.get(sub_agent_type)
        if sub_agent_url:
            http_result = await self._send_via_http(agent, sub_agent_url, payload)
            if http_result:
                return {"delivered": True, "channel": "http", "error": None}

        # ── Échec total ──
        error_msg = f"Delivery failed for action {action_id} to agent {agent.agent_id} (WS + HTTP)"
        logger.error(error_msg)

        # Broadcaster l'échec sur le dashboard
        await ws_manager.broadcast("action_delivery_failed", {
            "action_id": action_id,
            "agent_id": agent.agent_id,
            "action_type": action_type,
            "error": error_msg,
        })

        return {"delivered": False, "channel": "none", "error": error_msg}

    async def _send_via_ws(self, agent: AgentInfo, payload: dict, action_id: str) -> bool:
        """
        Envoie un ordre via WebSocket et attend un ACK pendant 5 secondes max.
        Retourne True si l'ACK est reçu à temps.
        """
        if agent.ws_connection is None:
            return False

        # Créer l'Event pour attendre l'ACK
        ack_event = asyncio.Event()
        self._pending_acks[action_id] = ack_event

        try:
            # Envoyer le message
            await agent.ws_connection.send_text(json.dumps(payload, default=str))
            logger.debug("WS sent action %s to agent %s", action_id, agent.agent_id)

            # Attendre l'ACK avec timeout de 5 secondes
            await asyncio.wait_for(ack_event.wait(), timeout=5.0)
            logger.info("WS ACK received for action %s from agent %s", action_id, agent.agent_id)
            return True

        except asyncio.TimeoutError:
            logger.warning("WS ACK timeout (5s) for action %s from agent %s", action_id, agent.agent_id)
            return False
        except Exception as exc:
            logger.warning("WS send failed for action %s: %s", action_id, exc)
            return False
        finally:
            self._pending_acks.pop(action_id, None)

    async def _send_via_http(self, agent: AgentInfo, sub_agent_url: str, payload: dict) -> bool:
        """
        Envoie un ordre via HTTP POST vers le sous-agent (fallback).
        Retourne True si la requête réussit (2xx).
        """
        url = f"{sub_agent_url}/execute"
        action_id = payload.get("action_id", "unknown")
        headers = {
            "X-Alice-Token": agent.token,
            "X-Action-Id": action_id,
        }

        # Adapter le payload au format attendu par l'endpoint /execute de l'agent
        http_payload = {
            "action_id": action_id,
            "action_type": payload.get("action_type", ""),
            "params": payload.get("params", {}),
            "timeout": payload.get("timeout", 30),
        }

        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=http_payload, headers=headers)
                resp.raise_for_status()
                logger.info("HTTP fallback delivered action %s to %s", action_id, url)
                return True
        except Exception as exc:
            logger.warning("HTTP fallback failed for action %s to %s: %s", action_id, url, exc)
            return False

    # ──────────── Traitement des messages entrants ────────────

    async def handle_agent_message(self, agent_id: str, message: dict[str, Any]) -> dict[str, Any] | None:
        """
        Dispatche un message reçu d'un agent via WebSocket.

        Types supportés :
          - heartbeat     → met à jour le registre, retourne heartbeat_ack
          - ack           → déclenche l'Event d'ACK en attente
          - action_result → traite le résultat d'exécution
        """
        msg_type = message.get("type", "unknown")

        if msg_type == "heartbeat":
            return await self._handle_heartbeat(agent_id, message)
        elif msg_type == "ack":
            self._handle_ack(message)
            return None  # Pas de réponse nécessaire
        elif msg_type == "action_result":
            await self._handle_action_result(agent_id, message)
            return None  # Pas de réponse nécessaire
        else:
            logger.warning("Unknown message type '%s' from agent %s", msg_type, agent_id)
            return None

    async def _handle_heartbeat(self, agent_id: str, message: dict[str, Any]) -> dict[str, Any]:
        """Traite un heartbeat agent et retourne un heartbeat_ack."""
        metrics = message.get("metrics", {})
        await agent_registry.update_heartbeat(agent_id, metrics)

        logger.debug(
            "Heartbeat from %s — CPU=%.1f%%, MEM=%.1f%%",
            agent_id,
            metrics.get("cpu_percent", 0),
            metrics.get("memory_percent", 0),
        )

        # Construire le heartbeat_ack avec les actions en attente
        # (si l'agent s'était déconnecté et a manqué des ordres)
        return {
            "type": "heartbeat_ack",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "pending_actions": [],  # Sera rempli par le système de queuing si nécessaire
        }

    def _handle_ack(self, message: dict[str, Any]) -> None:
        """Traite un ACK de réception d'action."""
        action_id = message.get("action_id")
        if not action_id:
            return

        event = self._pending_acks.get(action_id)
        if event:
            event.set()
            logger.debug("ACK processed for action %s", action_id)

    async def _handle_action_result(self, agent_id: str, message: dict[str, Any]) -> None:
        """
        Traite le résultat d'exécution d'une action.

        - Met à jour le statut dans Elasticsearch (index alice-actions)
        - Broadcaster sur le dashboard WS
        - Déclenche l'Event de résultat si quelqu'un attend dessus
        """
        action_id = message.get("action_id", "unknown")
        success = message.get("success", False)
        output = message.get("output", "")
        error = message.get("error")
        duration_ms = message.get("duration_ms", 0)
        sub_agent = message.get("sub_agent", "unknown")
        simulated = message.get("simulated", False)

        logger.info(
            "Action result from %s: action=%s, success=%s, duration=%dms",
            agent_id, action_id, success, duration_ms,
        )

        # Indexer dans ES
        result_doc = {
            "action_id": action_id,
            "agent_id": agent_id,
            "sub_agent": sub_agent,
            "success": success,
            "output": output,
            "error": error,
            "duration_ms": duration_ms,
            "simulated": simulated,
            "executed_at": message.get("executed_at", datetime.now(timezone.utc).isoformat()),
            "received_at": datetime.now(timezone.utc).isoformat(),
        }
        es_index = getattr(settings, "ES_INDEX_ACTIONS", "alice-actions")
        await es_service.index_document(es_index, result_doc, f"{action_id}-result")

        # Broadcaster sur le dashboard
        await ws_manager.broadcast("action_executed", {
            "action_id": action_id,
            "agent_id": agent_id,
            "sub_agent": sub_agent,
            "success": success,
            "output": output,
            "simulated": simulated,
            "duration_ms": duration_ms,
        })

        # Stocker le résultat et déclencher l'event si quelqu'un attend
        self._action_results[action_id] = message
        result_event = self._result_events.get(action_id)
        if result_event:
            result_event.set()

    # ──────────── Attendre un résultat ────────────

    async def wait_for_result(self, action_id: str, timeout: float = 60.0) -> dict[str, Any] | None:
        """
        Attend le résultat d'une action avec un timeout.
        Utile pour les actions bloquantes dans le graphe LangGraph.
        """
        # Vérifier si le résultat est déjà là
        if action_id in self._action_results:
            return self._action_results.pop(action_id)

        # Créer un event et attendre
        event = asyncio.Event()
        self._result_events[action_id] = event

        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            return self._action_results.pop(action_id, None)
        except asyncio.TimeoutError:
            logger.warning("Timeout waiting for result of action %s", action_id)
            return None
        finally:
            self._result_events.pop(action_id, None)


# ── Singleton ──
agent_communicator = AgentCommunicator()
