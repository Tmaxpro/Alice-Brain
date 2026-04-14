"""
ALICE Brain — Report Agent (agents/report.py)
────────────────────────────────────────────
Génère un rapport d'incident professionnel en Markdown via le LLM.
Stocke le rapport dans ES et broadcast la clôture via WebSocket.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from services.elasticsearch import es_service
from services.llm_factory import llm
from services.websocket_manager import ws_manager
from config import settings

logger = logging.getLogger(__name__)

REPORT_PROMPT = """Tu es un expert en cybersécurité.
Génère un rapport d'incident professionnel et auditable en français.
Format Markdown avec ces sections :

# Rapport d'Incident — [ID] — [DATE]
## Résumé Exécutif
## Chronologie Détaillée
## Analyse Technique (TTPs MITRE ATT&CK)
## Indicateurs de Compromission (IOCs)
## Actions de Réponse Prises
## Recommandations de Remédiation
## Leçons Apprises
## Conformité (NIS2, ISO 27001)

Sois précis, factuel et professionnel."""


async def run_report(state: dict[str, Any]) -> dict:
    """Nœud LangGraph : génération du rapport final."""
    incident_id = state.get("incident_id", "?")
    timeline = list(state.get("timeline", []))

    logger.info("[report] Generating report for incident %s", incident_id)

    # Construire le contexte complet de l'incident
    incident_context = {
        "incident_id": incident_id,
        "alert": state["alert"].model_dump() if state.get("alert") else None,
        "investigation": state["investigation"].model_dump() if state.get("investigation") else None,
        "response_plan": state["response_plan"].model_dump() if state.get("response_plan") else None,
        "actions_executed": [a.model_dump() for a in state.get("actions_executed", [])],
        "timeline": timeline,
    }

    report_md = await _generate_report(incident_context, incident_id)

    # Stocker dans ES
    doc = {
        "incident_id": incident_id,
        "report_markdown": report_md,
        "created_at": datetime.utcnow().isoformat(),
    }
    await es_service.index_document(settings.ES_INDEX_REPORTS, doc, incident_id)

    # WS broadcast
    preview = report_md[:200] + "..." if len(report_md) > 200 else report_md
    await ws_manager.broadcast("incident_closed", {
        "incident_id": incident_id,
        "report_preview": preview,
    })

    timeline.append({
        "timestamp": datetime.utcnow().isoformat(),
        "event": "report_generated",
        "details": f"{len(report_md)} chars",
    })

    logger.info("[report] Report generated for %s (%d chars)", incident_id, len(report_md))
    return {"report": report_md, "status": "closed", "timeline": timeline}


async def _generate_report(context: dict[str, Any], incident_id: str) -> str:
    """Appel LLM pour le rapport, avec fallback en dur."""
    human_msg = f"Incident complet :\n{json.dumps(context, default=str)}"

    for attempt in range(2):
        try:
            resp = await llm.ainvoke([
                SystemMessage(content=REPORT_PROMPT),
                HumanMessage(content=human_msg),
            ])
            return resp.content.strip()
        except Exception as exc:
            logger.error("Report LLM attempt %d failed: %s", attempt + 1, exc)
            if attempt == 0:
                human_msg += "\n\nRéessaie de générer le rapport."

    # Fallback codé en dur
    return f"""# Rapport d'Incident — {incident_id} — {datetime.utcnow().strftime('%Y-%m-%d %H:%M')}

## Résumé Exécutif
Rapport automatique généré sans IA (le LLM n'était pas disponible).

## Données brutes
```json
{json.dumps(context, default=str, indent=2)[:2000]}
```

## Note
Ce rapport nécessite une revue manuelle par un analyste SOC.
"""
