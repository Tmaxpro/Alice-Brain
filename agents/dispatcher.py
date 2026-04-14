"""
ALICE Brain — Dispatcher Agent (agents/dispatcher.py)
────────────────────────────────────────────────────
Exécute les actions AUTO immédiatement.
Met en attente les actions CRITIQUES (via approval_queue).
Respecte ALICE_SIMULATION_MODE.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

import httpx

from models.action import Action
from services.elasticsearch import es_service
from services.websocket_manager import ws_manager
from config import settings

logger = logging.getLogger(__name__)

# Map target_agent → URL de base
AGENT_URLS = {
    "endpoint_agent": settings.ENDPOINT_AGENT_URL,
    "network_agent": settings.NETWORK_AGENT_URL,
    "notif_agent": settings.NOTIF_AGENT_URL,
}


async def _execute_action_real(action: Action) -> tuple[bool, str]:
    """Exécute une action via HTTP POST vers l'agent client-side (mode production)."""
    base_url = AGENT_URLS.get(action.target_agent, settings.ENDPOINT_AGENT_URL)
    url = f"{base_url}/execute"
    payload = {"action_type": action.type, "params": action.params}

    # Retry avec backoff exponentiel : 1s, 2s, 4s
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.post(url, json=payload)
                resp.raise_for_status()
                return True, resp.text
        except Exception as exc:
            wait = 2 ** attempt
            logger.warning("Action %s attempt %d failed: %s — retrying in %ds", action.id, attempt + 1, exc, wait)
            await asyncio.sleep(wait)

    return False, "Max retries exceeded"


async def _execute_action_simulated(action: Action) -> tuple[bool, str]:
    """Simule l'exécution d'une action (mode hackathon)."""
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
        "timestamp": datetime.utcnow().isoformat(),
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

            # Exécuter
            if settings.ALICE_SIMULATION_MODE:
                success, output = await _execute_action_simulated(action)
            else:
                success, output = await _execute_action_real(action)

            action.executed = True
            action.status = "executed" if success else "failed"
            action.output = output

            # Mettre à jour dans ES
            await _log_action_to_es(action, incident_id)

            # WS broadcast
            await ws_manager.broadcast("action_executed", {
                "action_id": action.id,
                "incident_id": incident_id,
                "success": success,
                "output": output,
                "simulated": settings.ALICE_SIMULATION_MODE,
            })

            timeline.append({
                "timestamp": datetime.utcnow().isoformat(),
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
                    success, output = await _execute_action_real(action)

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
                    "timestamp": datetime.utcnow().isoformat(),
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
                    "timestamp": datetime.utcnow().isoformat(),
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
