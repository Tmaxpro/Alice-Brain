from fastapi import APIRouter, HTTPException
from typing import List, Dict, Any
from models.incident import IncidentState
from agents.orchestrator import orchestrator
from services.elasticsearch import es_service

router = APIRouter(prefix="/api/incidents", tags=["incidents"])

@router.get("", response_model=List[IncidentState])
async def list_incidents():
    """List all incidents from Orchestrator memory."""
    return list(orchestrator.running_incidents.values())

@router.get("/{incident_id}", response_model=IncidentState)
async def get_incident(incident_id: str):
    """Get a specific incident."""
    if incident_id not in orchestrator.running_incidents:
        # Fallback to ES
        doc = await es_service.get_document("alice-incidents", incident_id)
        if doc:
            return doc
        raise HTTPException(status_code=404, detail="Incident not found")
    return orchestrator.running_incidents[incident_id]

@router.get("/{incident_id}/report")
async def get_incident_report(incident_id: str):
    """Get markdown report for an incident."""
    doc = await es_service.get_document("alice-reports", incident_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Report not generated yet or not found")
    return {"markdown": doc.get("report_markdown", "")}

@router.post("/manual")
async def trigger_manual_alert(payload: Dict[str, Any]):
    """Inject a manual alert to trigger the workflow (for demos)."""
    from models.alert import Alert
    try:
        alert = Alert(**payload)
        await orchestrator.process_new_alert(alert)
        return {"status": "accepted", "alert_id": alert.id}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
