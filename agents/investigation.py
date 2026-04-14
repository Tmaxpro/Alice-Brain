"""
ALICE Brain — Investigation Agent (agents/investigation.py)
──────────────────────────────────────────────────────────
Enrichit l'alerte via AbuseIPDB + corrélation ES + analyse narrative LLM.
Retourne un dict de mise à jour du state AliceState.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any

from langchain_core.messages import SystemMessage, HumanMessage

from models.alert import Alert
from models.incident import Investigation, IOC
from services.abuseipdb import abuseipdb_client
from services.elasticsearch import es_service
from services.llm_factory import llm
from services.websocket_manager import ws_manager
from config import settings

logger = logging.getLogger(__name__)

INVESTIGATION_PROMPT = """Tu es un analyste SOC senior expert en threat intelligence.
Analyse cet incident et retourne UNIQUEMENT ce JSON strict, sans markdown :
{
  "summary": "résumé en 3 phrases max",
  "mitre_ttps": ["TA0001", "T1110"],
  "iocs": [{"type": "ip", "value": "1.2.3.4", "context": "source de l'attaque"}],
  "confidence": "HIGH",
  "next_likely_action": "prédiction comportement attaquant"
}"""


async def run_investigation(state: dict[str, Any]) -> dict:
    """Nœud LangGraph : enrichissement + analyse narrative."""
    alert: Alert | None = state.get("alert")
    incident_id: str = state.get("incident_id", "?")
    timeline = list(state.get("timeline", []))

    if not alert:
        return {"status": "planning", "error": "No alert to investigate"}

    logger.info("[investigation] Starting for incident %s (IP: %s)", incident_id, alert.source_ip)

    # ── 1. Enrichissement AbuseIPDB ──
    abuse_data = await abuseipdb_client.check_ip(alert.source_ip)
    abuse_score = abuse_data.get("abuseConfidenceScore", 0)

    # ── 2. Corrélation temporelle ES (24h) ──
    correlated_logs = await es_service.get_logs_for_ip(alert.source_ip, hours=24)
    freq_count = len(correlated_logs)

    # ── 3. Asset criticality (heuristique simple) ──
    is_critical_asset = any(
        kw in str(alert.raw_logs).lower()
        for kw in ("root", "admin", "bastion", "prod", "db")
    )
    asset_criticality = 80 if is_critical_asset else 30

    # ── 4. Calcul risk_score ──
    freq_score = min(100, freq_count * 2)  # Cap à 100
    risk_score = round(
        (abuse_score * 0.4) + (freq_score * 0.3) + (asset_criticality * 0.3), 1
    )
    risk_score = min(100.0, risk_score)

    # ── 5. Analyse narrative LLM ──
    context_str = json.dumps({
        "alert": alert.model_dump(),
        "abuse_enrichment": abuse_data,
        "correlated_events_24h": freq_count,
        "asset_criticality": "HIGH" if is_critical_asset else "LOW",
        "risk_score": risk_score,
    }, default=str)

    analysis = await _call_llm_investigation(context_str)

    # ── 6. Construire l'objet Investigation ──
    iocs = [
        IOC(type=i.get("type", "ip"), value=i.get("value", ""), context=i.get("context", ""))
        for i in analysis.get("iocs", [])
    ]

    investigation = Investigation(
        alert=alert,
        enrichment={"abuseipdb": abuse_data, "correlated_events_24h": freq_count},
        narrative=analysis.get("summary", ""),
        mitre_ttps=analysis.get("mitre_ttps", []),
        iocs=iocs,
        confidence=analysis.get("confidence", "MEDIUM"),
        risk_score=risk_score,
        next_likely_action=analysis.get("next_likely_action", ""),
    )

    timeline.append({
        "timestamp": datetime.utcnow().isoformat(),
        "event": "investigation_complete",
        "details": f"risk_score={risk_score}, ttps={investigation.mitre_ttps}",
    })

    # WS broadcast
    await ws_manager.broadcast("investigation_done", {
        "incident_id": incident_id,
        "risk_score": risk_score,
        "mitre_ttps": investigation.mitre_ttps,
    })

    logger.info("[investigation] Done for %s — risk_score=%.1f", incident_id, risk_score)
    return {"investigation": investigation, "status": "planning", "timeline": timeline}


async def _call_llm_investigation(context: str) -> dict[str, Any]:
    """Appel LLM avec retry JSON malformé + fallback codé en dur."""
    human_msg = f"Alert et enrichissement :\n{context}"

    for attempt in range(2):
        try:
            resp = await llm.ainvoke([
                SystemMessage(content=INVESTIGATION_PROMPT),
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
                human_msg += "\n\nERREUR : JSON invalide. Réponds UNIQUEMENT avec le JSON demandé, sans texte autour."
                continue
        except Exception as exc:
            logger.error("Investigation LLM failed: %s", exc)
            break

    # Fallback
    return {
        "summary": "Analyse automatique non disponible — investigation manuelle recommandée.",
        "mitre_ttps": ["T1110"],
        "iocs": [],
        "confidence": "LOW",
        "next_likely_action": "Unknown",
    }
