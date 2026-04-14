"""
ALICE Brain — API Incidents (api/incidents.py)
────────────────────────────────────────────
Endpoints REST pour la consultation et l'injection manuelle d'alertes.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from models.alert import Alert
from models.incident import IncidentState
from agents.orchestrator import incidents_registry, process_alert
from services.elasticsearch import es_service
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["incidents"])


@router.get("/incidents", response_model=list[IncidentState])
async def list_incidents(
    status: str | None = Query(None, description="Filtrer par status"),
    severity: str | None = Query(None, description="Filtrer par severity"),
):
    """Liste tous les incidents, avec filtres optionnels."""
    incidents = list(incidents_registry.values())

    if status:
        incidents = [i for i in incidents if i.status == status]
    if severity:
        incidents = [i for i in incidents if i.alert and i.alert.severity == severity]

    return incidents


@router.get("/incidents/{incident_id}", response_model=IncidentState)
async def get_incident(incident_id: str):
    """Détail complet d'un incident."""
    if incident_id in incidents_registry:
        return incidents_registry[incident_id]

    # Fallback ES
    doc = await es_service.get_document(settings.ES_INDEX_INCIDENTS, incident_id)
    if doc:
        return doc
    raise HTTPException(status_code=404, detail="Incident not found")


@router.get("/incidents/{incident_id}/report")
async def get_incident_report(incident_id: str):
    """Récupère le rapport Markdown d'un incident."""
    # D'abord vérifier en mémoire
    if incident_id in incidents_registry and incidents_registry[incident_id].report:
        return {"incident_id": incident_id, "markdown": incidents_registry[incident_id].report}

    # Sinon chercher dans ES
    doc = await es_service.get_document(settings.ES_INDEX_REPORTS, incident_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Report not found or not yet generated")
    return {"incident_id": incident_id, "markdown": doc.get("report_markdown", "")}


@router.post("/alerts/manual")
async def inject_manual_alert(payload: dict[str, Any]):
    """
    Injection manuelle d'une alerte (pour les démos).
    Body minimal : {"type": "brute_force_ssh", "severity": "HIGH", "source_ip": "1.2.3.4"}
    """
    try:
        alert = Alert(**payload)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid alert payload: {exc}")

    logger.info("Manual alert injected: type=%s, ip=%s", alert.type, alert.source_ip)

    # Lancer le traitement en background pour ne pas bloquer la requête
    import asyncio
    asyncio.create_task(process_alert(alert))

    return {"status": "accepted", "alert_id": alert.id, "message": "Alert injected into ALICE pipeline"}
