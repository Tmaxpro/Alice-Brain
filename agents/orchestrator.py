"""
ALICE Brain — Orchestrator (agents/orchestrator.py)
──────────────────────────────────────────────────
Graphe LangGraph StateGraph qui relie les 5 agents :
  detect → deduplicate → investigate → plan → dispatch ↔ wait_approval → report

Utilise MemorySaver pour la persistance des états en cours.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Literal, TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import MemorySaver

from models.alert import Alert
from models.action import Action
from models.incident import Investigation, IncidentState
from models.response_plan import ResponsePlan
from services.elasticsearch import es_service
from services.websocket_manager import ws_manager
from config import settings

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════
#  STATE GLOBAL LANGGRAPH
# ═══════════════════════════════════════

class AliceState(TypedDict):
    incident_id: str
    alert: Alert | None
    investigation: Investigation | None
    response_plan: ResponsePlan | None
    actions_pending: list[Action]
    actions_executed: list[Action]
    report: str | None
    status: Literal[
        "detecting", "investigating", "planning",
        "dispatching", "pending_approval", "reporting",
        "closed", "duplicate",
    ]
    timeline: list[dict[str, Any]]
    error: str | None


# ═══════════════════════════════════════
#  REGISTRE DES INCIDENTS (mémoire)
# ═══════════════════════════════════════

# incident_id → IncidentState (version Pydantic pour l'API)
incidents_registry: dict[str, IncidentState] = {}


def _now() -> str:
    return datetime.utcnow().isoformat()


# ═══════════════════════════════════════
#  NŒUDS DU GRAPHE
# ═══════════════════════════════════════

async def detection_node(state: AliceState) -> dict:
    """Nœud de passage — l'alerte est déjà injectée dans le state."""
    logger.info("[detect] Incident %s — alert type=%s", state["incident_id"], state["alert"].type if state["alert"] else "?")
    timeline = state.get("timeline", [])
    timeline.append({"timestamp": _now(), "event": "alert_received", "details": f"type={state['alert'].type}" if state['alert'] else ""})
    return {"status": "detecting", "timeline": timeline}


async def dedup_node(state: AliceState) -> dict:
    """
    Déduplique : si un incident ouvert existe pour la même IP + même type
    dans la fenêtre DEDUP_WINDOW_MINUTES → marque comme 'duplicate'.
    """
    alert = state["alert"]
    if not alert:
        return {"status": "duplicate"}

    window = timedelta(minutes=settings.DEDUP_WINDOW_MINUTES)
    now = datetime.utcnow()

    for inc in incidents_registry.values():
        if inc.status in ("closed", "duplicate"):
            continue
        if inc.id == state["incident_id"]:
            continue
        if (
            inc.alert
            and inc.alert.source_ip == alert.source_ip
            and inc.alert.type == alert.type
            and (now - inc.alert.timestamp) < window
        ):
            logger.info("[dedup] Duplicate detected for %s — skipping", alert.source_ip)
            return {"status": "duplicate"}

    return {"status": "investigating"}


async def investigation_node(state: AliceState) -> dict:
    """Délègue au module agents/investigation.py."""
    from agents.investigation import run_investigation
    return await run_investigation(state)


async def response_planner_node(state: AliceState) -> dict:
    """Délègue au module agents/response_planner.py."""
    from agents.response_planner import run_response_planner
    return await run_response_planner(state)


async def dispatcher_node(state: AliceState) -> dict:
    """Délègue au module agents/dispatcher.py."""
    from agents.dispatcher import run_dispatcher
    return await run_dispatcher(state)


async def approval_gate_node(state: AliceState) -> dict:
    """
    Nœud bloquant : attend l'approbation humaine pour chaque action pending.
    Utilise le mécanisme asyncio.Queue de services/approval_queue.py.
    """
    from services.approval_queue import wait_for_approval

    pending = list(state.get("actions_pending", []))
    executed = list(state.get("actions_executed", []))
    timeline = list(state.get("timeline", []))

    still_pending: list[Action] = []

    for action in pending:
        if not action.requires_approval:
            # Shouldn't happen here, but safety
            still_pending.append(action)
            continue

        logger.info("[approval_gate] Waiting for approval on action %s (%s)", action.id, action.type)
        approved = await wait_for_approval(action.id, timeout=300)

        if approved:
            action.approved = True
            action.status = "approved"
            timeline.append({"timestamp": _now(), "event": "action_approved", "details": f"{action.type} ({action.id})"})
            still_pending.append(action)  # Will be executed in next dispatcher pass
        else:
            action.status = "rejected" if not approved else "timeout"
            timeline.append({"timestamp": _now(), "event": "action_rejected", "details": f"{action.type} ({action.id})"})
            executed.append(action)  # Move to executed (as rejected/timeout)

    return {
        "actions_pending": still_pending,
        "actions_executed": executed,
        "status": "dispatching",
        "timeline": timeline,
    }


async def report_node(state: AliceState) -> dict:
    """Délègue au module agents/report.py."""
    from agents.report import run_report
    return await run_report(state)


# ═══════════════════════════════════════
#  CONSTRUCTION DU GRAPHE
# ═══════════════════════════════════════

def _build_graph() -> StateGraph:
    graph = StateGraph(AliceState)

    # Nœuds
    graph.add_node("detect", detection_node)
    graph.add_node("deduplicate", dedup_node)
    graph.add_node("investigate", investigation_node)
    graph.add_node("plan", response_planner_node)
    graph.add_node("dispatch", dispatcher_node)
    graph.add_node("wait_approval", approval_gate_node)
    graph.add_node("report", report_node)

    # Edges
    graph.add_edge(START, "detect")
    graph.add_edge("detect", "deduplicate")

    # Après déduplication
    graph.add_conditional_edges(
        "deduplicate",
        lambda s: "skip" if s["status"] == "duplicate" else "continue",
        {"skip": END, "continue": "investigate"},
    )

    graph.add_edge("investigate", "plan")
    graph.add_edge("plan", "dispatch")

    # Après dispatch
    graph.add_conditional_edges(
        "dispatch",
        lambda s: "needs_approval" if s.get("actions_pending") else "auto_complete",
        {"needs_approval": "wait_approval", "auto_complete": "report"},
    )

    # Retour après approbation
    graph.add_edge("wait_approval", "dispatch")

    graph.add_edge("report", END)

    return graph


# Compilation avec checkpointer MemorySaver
checkpointer = MemorySaver()
_graph = _build_graph()
alice_graph = _graph.compile(checkpointer=checkpointer)


# ═══════════════════════════════════════
#  POINT D'ENTRÉE PUBLIC
# ═══════════════════════════════════════

async def process_alert(alert: Alert) -> str:
    """
    Injecte une alerte dans le graphe LangGraph et exécute le pipeline complet.
    Retourne l'incident_id.
    """
    incident_id = str(uuid.uuid4())

    # Créer l'entrée dans le registre
    incident = IncidentState(
        id=incident_id,
        alert=alert,
        status="detecting",
        timeline=[{"timestamp": _now(), "event": "incident_created", "details": ""}],
    )
    incidents_registry[incident_id] = incident

    # Indexer dans ES
    await es_service.index_document(
        settings.ES_INDEX_INCIDENTS, incident.model_dump(), incident_id
    )

    # Broadcaster via WS
    await ws_manager.broadcast("new_alert", {
        "incident_id": incident_id,
        "severity": alert.severity,
        "type": alert.type,
        "source_ip": alert.source_ip,
    })

    # Construire le state initial
    initial_state: AliceState = {
        "incident_id": incident_id,
        "alert": alert,
        "investigation": None,
        "response_plan": None,
        "actions_pending": [],
        "actions_executed": [],
        "report": None,
        "status": "detecting",
        "timeline": [],
        "error": None,
    }

    # Exécuter le graphe
    config = {"configurable": {"thread_id": incident_id}}
    try:
        final_state = await alice_graph.ainvoke(initial_state, config=config)

        # Mettre à jour le registre avec le state final
        incident.status = final_state.get("status", "closed")
        incident.investigation = final_state.get("investigation")
        incident.response_plan = final_state.get("response_plan")
        incident.actions_pending = final_state.get("actions_pending", [])
        incident.actions_executed = final_state.get("actions_executed", [])
        incident.report = final_state.get("report")
        incident.timeline = final_state.get("timeline", [])
        incident.error = final_state.get("error")

    except Exception as exc:
        logger.exception("Graph execution failed for incident %s", incident_id)
        incident.status = "closed"
        incident.error = str(exc)

    # Mise à jour finale dans ES
    await es_service.index_document(
        settings.ES_INDEX_INCIDENTS, incident.model_dump(), incident_id
    )

    return incident_id
