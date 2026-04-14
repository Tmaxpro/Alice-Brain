from fastapi import APIRouter, HTTPException
from agents.orchestrator import orchestrator

router = APIRouter(prefix="/api/actions", tags=["actions"])

@router.post("/{action_id}/approve")
async def approve_action(action_id: str, incident_id: str):
    """Approuver une action critique."""
    # incident_id should ideally be in path or query
    # We pass it in query here: ?incident_id=...
    success = await orchestrator.approve_action_and_resume(incident_id, action_id)
    if not success:
        raise HTTPException(status_code=404, detail="Action or incident not found, or action not pending.")
    
    return {"status": "approved", "action_id": action_id}
