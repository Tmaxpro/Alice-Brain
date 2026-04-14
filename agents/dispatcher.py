"""
ALICE Brain — Dispatcher Agent (agents/dispatcher.py)
────────────────────────────────────────────────────
Exécute les actions AUTO immédiatement.
Met en attente les actions CRITIQUES (via approval_queue).
Respecte ALICE_SIMULATION_MODE.

Utilise le AgentCommunicator pour l'envoi via WS (principal) + HTTP (fallback)
et le AgentRegistry pour le routage intelligent des actions vers le bon agent.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from models.action import Action
from services.elasticsearch import es_service
from services.websocket_manager import ws_manager
from services.agent_registry import agent_registry
from services.agent_communicator import agent_communicator, NoAgentAvailableError
from config import settings

logger = logging.getLogger(__name__)

# ── Mapping legacy target_agent → sub-agent type ──
TARGET_AGENT_MAP: dict[str, str] = {
    "endpoint_agent": "endpoint",
    "network_agent": "network",
    "log_collector": "collector",
    "notif_agent": "notif",
}


async def _execute_action_via_agents(action: Action) -> tuple[bool, str]:
    """
    Exécute une action via le système de communication dynamique.

    Flux :
      1. Résoudre le meilleur agent via le registre (IP → subnet → capability)
      2. Envoyer l'ordre via WS (principal) puis HTTP (fallback)
      3. Attendre le résultat si ALICE_SIMULATION_MODE est désactivé
    """
    sub_agent_type = TARGET_AGENT_MAP.get(action.target_agent, action.target_agent)

    # Déterminer la priorité selon requires_approval
    priority = "CRITICAL" if action.requires_approval else "NORMAL"

    # Extraire l'IP cible pour le routage
    target_ip = action.params.get("ip") or action.params.get("target_ip")

    try:
        # 1. Trouver le meilleur agent
        agent = await agent_communicator.route_action(
            action_type=action.type,
            target_agent_name=action.target_agent,
            params=action.params,
            incident_source_ip=target_ip,
        )

        logger.info(
            "[dispatcher] Routing action '%s' (id=%s) → agent %s (%s:%s)",
            action.type, action.id, agent.agent_id, agent.hostname, agent.ip,
        )

        # 2. Envoyer l'ordre
        delivery = await agent_communicator.send_action(
            agent=agent,
            action_id=action.id,
            action_type=action.type,
            target_sub_agent=sub_agent_type,
            params=action.params,
            priority=priority,
            timeout=30,
        )

        if not delivery["delivered"]:
            return False, f"Delivery failed: {delivery.get('error', 'unknown')}"

        channel = delivery["channel"]
        logger.info(
            "[dispatcher] Action %s delivered via %s to agent %s",
            action.id, channel, agent.agent_id,
        )

        # 3. Attendre le résultat (timeout 60s)
        result = await agent_communicator.wait_for_result(action.id, timeout=60.0)

        if result:
            success = result.get("success", False)
            output = result.get("output", "")
            error = result.get("error")
            if error:
                output = f"{output} | Error: {error}"
            return success, output
        else:
            # Timeout en attendant le résultat — considéré comme un succès
            # de livraison (l'action est en cours côté agent)
            return True, f"Action delivered via {channel}, awaiting execution result"

    except NoAgentAvailableError as exc:
        logger.warning("[dispatcher] No agent available: %s", exc)
        return False, str(exc)
    except Exception as exc:
        logger.error("[dispatcher] Execution error: %s", exc)
        return False, f"Dispatcher error: {exc}"


async def _execute_action_simulated(action: Action) -> tuple[bool, str]:
    """
    Simule l'exécution d'une action (mode hackathon/demo).

    En mode simulation, la COMMUNICATION est réelle (WS/HTTP),
    seule l'exécution système est mockée.
    Si aucun agent n'est connecté, on simule entièrement en local.
    """
    # Tenter d'envoyer via un vrai agent (communication réelle, exécution simulée)
    online_agents = agent_registry.get_online_agents()
    if online_agents:
        success, output = await _execute_action_via_agents(action)
        if success:
            return True, f"[SIMULATION] {output}"

    # Fallback : simulation locale sans agent réel
    await asyncio.sleep(0.5)  # Simule la latence réseau
    output = f"[SIMULATION] Action '{action.type}' exécutée avec succès — params={action.params}"
    logger.info(output)
    return True, output


async def _log_action_to_es(action: Action, incident_id: str) -> None:
    """Log l'action dans ES AVANT exécution (contrainte : loguer avant d'exécuter)."""
    doc = {
        "action_id": action.id,
        "incident_id": incident_id,
        "type": action.type,
        "target_agent": action.target_agent,
        "params": action.params,
        "status": action.status,
        "requires_approval": action.requires_approval,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    await es_service.index_document(settings.ES_INDEX_ACTIONS, doc, action.id)


async def run_dispatcher(state: dict[str, Any]) -> dict:
    """Nœud LangGraph : dispatch des actions."""
    incident_id = state.get("incident_id", "?")
    pending = list(state.get("actions_pending", []))
    executed = list(state.get("actions_executed", []))
    timeline = list(state.get("timeline", []))

    logger.info("[dispatcher] Processing %d pending actions for %s", len(pending), incident_id)

    still_pending: list[Action] = []

    for action in pending:
        # ── Actions AUTO ──
        if not action.requires_approval:
            # Log AVANT exécution
            action.status = "executing"
            await _log_action_to_es(action, incident_id)

            # Exécuter via le système de communication dynamique
            if settings.ALICE_SIMULATION_MODE:
                success, output = await _execute_action_simulated(action)
            else:
                success, output = await _execute_action_via_agents(action)

            action.executed = True
            action.status = "executed" if success else "failed"
            action.output = output

            # Mettre à jour dans ES
            await _log_action_to_es(action, incident_id)

            # WS broadcast sur le dashboard
            await ws_manager.broadcast("action_executed", {
                "action_id": action.id,
                "incident_id": incident_id,
                "success": success,
                "output": output,
                "simulated": settings.ALICE_SIMULATION_MODE,
            })

            timeline.append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event": "action_executed",
                "details": f"{action.type} → {'OK' if success else 'FAIL'}",
            })

            executed.append(action)

        # ── Actions CRITIQUES ──
        elif action.requires_approval:
            if action.status == "approved":
                # L'action a été approuvée dans approval_gate — on l'exécute
                action.status = "executing"
                await _log_action_to_es(action, incident_id)

                if settings.ALICE_SIMULATION_MODE:
                    success, output = await _execute_action_simulated(action)
                else:
                    success, output = await _execute_action_via_agents(action)

                action.executed = True
                action.status = "executed" if success else "failed"
                action.output = output

                await _log_action_to_es(action, incident_id)
                await ws_manager.broadcast("action_executed", {
                    "action_id": action.id,
                    "incident_id": incident_id,
                    "success": success,
                    "output": output,
                    "simulated": settings.ALICE_SIMULATION_MODE,
                })

                timeline.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "critical_action_executed",
                    "details": f"{action.type} (approved) → {'OK' if success else 'FAIL'}",
                })
                executed.append(action)

            else:
                # Première passe : marquer pending_approval et notifier
                action.status = "pending_approval"
                await _log_action_to_es(action, incident_id)

                await ws_manager.broadcast("approval_required", {
                    "incident_id": incident_id,
                    "action_id": action.id,
                    "action_type": action.type,
                    "target": action.params,
                    "reason": action.reason,
                })

                timeline.append({
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "event": "approval_requested",
                    "details": f"{action.type} ({action.id})",
                })

                still_pending.append(action)

    # Déterminer le status suivant
    if still_pending:
        status = "pending_approval"
    else:
        status = "reporting"

    logger.info("[dispatcher] Done — %d executed, %d pending approval", len(executed), len(still_pending))
    return {
        "actions_pending": still_pending,
        "actions_executed": executed,
        "status": status,
        "timeline": timeline,
    }
