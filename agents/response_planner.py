"""
ALICE Brain — Response Planner Agent (agents/response_planner.py)
────────────────────────────────────────────────────────────────
Génère un plan de réponse structuré PICERL via le LLM.
Sépare les actions AUTO des actions CRITIQUES.
Vérifie que les IPs protégées ne sont jamais ciblées.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from models.action import Action
from models.response_plan import ResponsePlan
from services.llm_factory import llm
from config import settings

logger = logging.getLogger(__name__)

PLANNER_PROMPT_TEMPLATE = """Tu es un expert en réponse aux incidents cybersécurité.
Génère un plan de réponse PICERL. Retourne UNIQUEMENT ce JSON strict, sans markdown :
{{
  "summary": "...",
  "phases": {{
    "preparation": ["..."],
    "identification": ["..."],
    "containment": ["..."],
    "eradication": ["..."],
    "recovery": ["..."],
    "lessons_learned": ["..."]
  }},
  "actions_auto": [
    {{
      "type": "block_ip",
      "target_agent": "network_agent",
      "params": {{"ip": "x.x.x.x", "duration": 3600}},
      "reason": "justification courte",
      "requires_approval": false
    }}
  ],
  "actions_critical": [
    {{
      "type": "isolate_host",
      "target_agent": "endpoint_agent",
      "params": {{"hostname": "server-1"}},
      "reason": "justification courte",
      "requires_approval": true
    }}
  ],
  "estimated_impact": "description de l'impact si non traité"
}}

Actions AUTO (sans validation) : block_ip, send_notification, collect_forensics
Actions CRITIQUES (validation analyste requise) : isolate_host, kill_process, disable_account, reset_firewall_rules

IMPORTANT : vérifie que les IPs dans les params ne sont pas dans cette liste protégée : {protected_ips}"""


def _parse_actions(raw_actions: list[dict], requires_approval: bool) -> list[Action]:
    """Parse la liste d'actions brutes du LLM en objets Action."""
    actions: list[Action] = []
    for a in raw_actions:
        actions.append(Action(
            id=str(uuid.uuid4()),
            type=a.get("type", "unknown"),
            target_agent=a.get("target_agent", "unknown"),
            params=a.get("params", {}),
            reason=a.get("reason", ""),
            requires_approval=requires_approval,
            status="pending" if not requires_approval else "pending_approval",
        ))
    return actions


def _filter_protected_ips(actions: list[Action]) -> list[Action]:
    """Supprime les actions qui ciblent des IPs protégées."""
    filtered: list[Action] = []
    for action in actions:
        target_ip = action.params.get("ip", "")
        if target_ip in settings.PROTECTED_IPS:
            logger.warning("BLOCKED: action %s targets protected IP %s — skipping", action.type, target_ip)
            continue
        hostname = action.params.get("hostname", "")
        if hostname in settings.PROTECTED_IPS:
            logger.warning("BLOCKED: action %s targets protected host %s — skipping", action.type, hostname)
            continue
        filtered.append(action)
    return filtered


async def run_response_planner(state: dict[str, Any]) -> dict:
    """Nœud LangGraph : génération du plan de réponse PICERL."""
    incident_id = state.get("incident_id", "?")
    investigation = state.get("investigation")
    timeline = list(state.get("timeline", []))

    if not investigation:
        logger.warning("[planner] No investigation data — using fallback plan")
        return _fallback_plan(incident_id, state, timeline)

    logger.info("[planner] Generating response plan for incident %s", incident_id)

    # Construire le contexte
    context = {
        "incident_id": incident_id,
        "narrative": investigation.narrative,
        "mitre_ttps": investigation.mitre_ttps,
        "risk_score": investigation.risk_score,
        "source_ip": investigation.alert.source_ip if investigation.alert else "unknown",
        "target_host": investigation.alert.target_host if investigation.alert else "unknown",
        "iocs": [ioc.model_dump() for ioc in investigation.iocs] if investigation.iocs else [],
    }

    plan_data = await _call_llm_planner(context)

    # Parser les actions
    actions_auto = _parse_actions(plan_data.get("actions_auto", []), requires_approval=False)
    actions_critical = _parse_actions(plan_data.get("actions_critical", []), requires_approval=True)

    # Filtrer les IPs protégées
    actions_auto = _filter_protected_ips(actions_auto)
    actions_critical = _filter_protected_ips(actions_critical)

    plan = ResponsePlan(
        incident_id=incident_id,
        severity=investigation.alert.severity if investigation.alert else "MEDIUM",
        summary=plan_data.get("summary", ""),
        phases=plan_data.get("phases", {}),
        actions_auto=actions_auto,
        actions_critical=actions_critical,
        estimated_impact=plan_data.get("estimated_impact", ""),
    )

    # Toutes les actions vont dans actions_pending pour le dispatcher
    all_pending = actions_auto + actions_critical

    timeline.append({
        "timestamp": datetime.utcnow().isoformat(),
        "event": "response_plan_created",
        "details": f"auto={len(actions_auto)}, critical={len(actions_critical)}",
    })

    logger.info("[planner] Plan created: %d auto, %d critical actions", len(actions_auto), len(actions_critical))
    return {
        "response_plan": plan,
        "actions_pending": all_pending,
        "status": "dispatching",
        "timeline": timeline,
    }


def _fallback_plan(incident_id: str, state: dict, timeline: list) -> dict:
    """Plan de secours si le LLM échoue ou si investigation absente."""
    alert = state.get("alert")
    source_ip = alert.source_ip if alert else "unknown"

    fallback_action = Action(
        id=str(uuid.uuid4()),
        type="send_notification",
        target_agent="notif_agent",
        params={"message": f"Incident {incident_id} — analyze manually"},
        reason="Fallback — no LLM response",
        requires_approval=False,
        status="pending",
    )
    plan = ResponsePlan(
        incident_id=incident_id,
        severity="MEDIUM",
        summary="Plan de secours (LLM indisponible)",
        actions_auto=[fallback_action],
        estimated_impact="Inconnu",
    )
    timeline.append({
        "timestamp": datetime.utcnow().isoformat(),
        "event": "response_plan_fallback",
        "details": "LLM unavailable",
    })
    return {
        "response_plan": plan,
        "actions_pending": [fallback_action],
        "status": "dispatching",
        "timeline": timeline,
    }


async def _call_llm_planner(context: dict[str, Any]) -> dict[str, Any]:
    """Appel LLM avec retry JSON malformé + fallback codé en dur."""
    system_msg = PLANNER_PROMPT_TEMPLATE.format(protected_ips=settings.PROTECTED_IPS)
    human_msg = f"Contexte de l'incident :\n{json.dumps(context, default=str)}"

    for attempt in range(2):
        try:
            resp = await llm.ainvoke([
                SystemMessage(content=system_msg),
                HumanMessage(content=human_msg),
            ])
            raw = resp.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()
            return json.loads(raw)
        except json.JSONDecodeError:
            if attempt == 0:
                human_msg += "\n\nERREUR : JSON invalide. Réponds UNIQUEMENT avec le JSON strict demandé."
                continue
        except Exception as exc:
            logger.error("Planner LLM failed: %s", exc)
            break

    return {
        "summary": "Plan de secours généré automatiquement",
        "phases": {},
        "actions_auto": [{"type": "send_notification", "target_agent": "notif_agent", "params": {"message": "Manual review needed"}, "reason": "LLM fallback"}],
        "actions_critical": [],
        "estimated_impact": "Inconnu — analyse manuelle requise",
    }
